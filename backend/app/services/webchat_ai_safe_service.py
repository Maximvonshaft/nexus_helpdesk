from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from sqlalchemy.orm import Session

from ..enums import EventType
from ..models import Ticket, TicketEvent
from ..settings import get_settings
from ..webchat_models import WebchatAITurn, WebchatConversation, WebchatMessage
from .nexus_osr.case_context import CaseContext
from .nexus_osr.escalation_orchestration_service import (
    EscalationOrchestrationAction,
    EscalationOrchestrationResult,
    evaluate_escalation_for_case,
)
from .nexus_osr.persistence import load_case_context, load_escalation_policies
from .nexus_osr.policies import evaluate_escalation
from .webchat_ai_service import (
    AI_AUTHOR_LABEL,
    _mark_ai_review_required,
    process_webchat_ai_reply_job as _legacy_process_webchat_ai_reply_job,
)
from .webchat_ai_turn_service import (
    AI_TURN_OPEN_STATUSES,
    cancel_open_ai_turns_for_handoff,
    complete_ai_turn_with_reply,
    is_ai_suspended_for_handoff,
    latest_visitor_message_id,
    mark_ai_turn_bridge_calling,
    mark_ai_turn_processing,
    suppress_stale_reply_if_needed,
)
from .webchat_osr_audit_service import audit_completed_webchat_ai_turn

settings = get_settings()
LOGGER = logging.getLogger("nexusdesk")
_TRUE_VALUES = {"1", "true", "yes", "on"}

HIGH_RISK_TERMS = (
    "refund", "compensation", "lost", "damaged", "customs", " tax ", "claim", "legal", "lawyer",
    "attorney", "solicitor", "court", " lawsuit ", " sue ", "pod", "proof of delivery",
    "delivered but not received", "address change", "change address", "complaint",
    "赔偿", "赔付", "退款", "丢件", "破损", "海关", "清关", "签收未收到", "改地址", "投诉", "索赔",
    "律师", "法律", "起诉", "法院",
)

_CUSTOMER_CANNOT_WAIT_RE = re.compile(
    r"\b(can't wait|cannot wait|cant wait|urgent|asap|immediately|right now|too late)\b|等不了|马上|立刻|加急|很急|等不及",
    re.IGNORECASE,
)


def _has_high_risk_intent(text: str | None) -> bool:
    normalized = f" {(text or '').lower()} "
    return any(term.lower() in normalized for term in HIGH_RISK_TERMS)


def _has_customer_wait_intent(text: str | None) -> bool:
    return bool(_CUSTOMER_CANNOT_WAIT_RE.search(str(text or "")))


def _env_enabled(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE_VALUES


def _osr_escalation_orchestration_enabled() -> bool:
    configured = getattr(settings, "osr_escalation_orchestration_enabled", None)
    if configured is not None:
        return bool(configured)
    return _env_enabled("OSR_ESCALATION_ORCHESTRATION_ENABLED", False)


def _status_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _load_context(db: Session, *, conversation_id: int, ticket_id: int, visitor_message_id: int) -> tuple[WebchatConversation, Ticket, WebchatMessage]:
    conversation = db.query(WebchatConversation).filter(WebchatConversation.id == conversation_id).first()
    if conversation is None:
        raise RuntimeError(f"webchat conversation not found: conversation_id={conversation_id}")
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if ticket is None:
        raise RuntimeError(f"ticket not found: ticket_id={ticket_id}")
    visitor_message = db.query(WebchatMessage).filter(WebchatMessage.id == visitor_message_id).first()
    if visitor_message is None:
        raise RuntimeError(f"visitor message not found: visitor_message_id={visitor_message_id}")
    if visitor_message.conversation_id != conversation.id or visitor_message.ticket_id != ticket.id:
        raise RuntimeError("webchat job payload mismatch")
    return conversation, ticket, visitor_message


def _open_turn_for_message(db: Session, *, conversation: WebchatConversation, visitor_message: WebchatMessage) -> WebchatAITurn | None:
    candidates = (
        db.query(WebchatAITurn)
        .filter(WebchatAITurn.conversation_id == conversation.id, WebchatAITurn.status.in_(AI_TURN_OPEN_STATUSES))
        .order_by(WebchatAITurn.id.asc())
        .all()
    )
    for turn in candidates:
        if turn.trigger_message_id == visitor_message.id or turn.latest_visitor_message_id == visitor_message.id or conversation.active_ai_turn_id == turn.id:
            return turn
    return None


def _agent_reply_exists(db: Session, *, conversation: WebchatConversation, visitor_message: WebchatMessage) -> bool:
    return bool(
        db.query(WebchatMessage.id)
        .filter(
            WebchatMessage.conversation_id == conversation.id,
            WebchatMessage.direction == "agent",
            WebchatMessage.id > visitor_message.id,
            WebchatMessage.author_label == AI_AUTHOR_LABEL,
        )
        .first()
    )


def _require_operator_review(db: Session, *, conversation: WebchatConversation, ticket: Ticket, visitor_message: WebchatMessage, reason: str, turn: WebchatAITurn | None = None) -> dict[str, Any]:
    if suppress_stale_reply_if_needed(db, conversation=conversation, turn=turn, reason="newer_message_before_review_commit"):
        LOGGER.info("webchat_ai_reply_suppressed_stale", extra={"event_payload": {"conversation_id": conversation.id, "ticket_id": ticket.id, "visitor_message_id": visitor_message.id, "ai_turn_id": turn.id if turn else None, "reason": "newer_message_before_review_commit"}})
        return {"status": "superseded", "reason": "newer_message_before_review_commit", "reply_source": "suppressed"}
    return _mark_ai_review_required(
        db,
        conversation=conversation,
        ticket=ticket,
        visitor_message=visitor_message,
        reason=reason,
        turn=turn,
        reply_source=reason,
    )


def _audit_webchat_osr_turn_non_blocking(db: Session, *, conversation: WebchatConversation, ticket: Ticket, visitor_message: WebchatMessage, turn: WebchatAITurn | None, result: dict[str, Any]) -> dict[str, Any] | None:
    if turn is None:
        return None
    try:
        with db.begin_nested():
            return audit_completed_webchat_ai_turn(
                db,
                conversation=conversation,
                ticket=ticket,
                visitor_message=visitor_message,
                turn=turn,
                result=result,
            )
    except Exception as exc:  # pragma: no cover - behavior covered by explicit monkeypatch test
        LOGGER.warning(
            "webchat_osr_audit_failed_non_blocking",
            extra={"event_payload": {"conversation_id": conversation.id, "ticket_id": ticket.id, "visitor_message_id": visitor_message.id, "ai_turn_id": turn.id if turn else None, "error_type": type(exc).__name__}},
        )
        return None


def _complete_turn_if_present(db: Session, *, conversation: WebchatConversation, ticket: Ticket, visitor_message: WebchatMessage, turn: WebchatAITurn | None, result: dict[str, Any], audit_after_complete: bool = True) -> dict[str, Any] | None:
    if turn is None:
        return None
    complete_ai_turn_with_reply(db, conversation=conversation, turn=turn, result=result)
    if not audit_after_complete:
        return None
    return _audit_webchat_osr_turn_non_blocking(
        db,
        conversation=conversation,
        ticket=ticket,
        visitor_message=visitor_message,
        turn=turn,
        result=result,
    )


def _case_context_for_webchat(db: Session, *, conversation: WebchatConversation, ticket: Ticket, visitor_message: WebchatMessage) -> CaseContext:
    existing = load_case_context(db, conversation_id=conversation.id, ticket_id=ticket.id)
    if existing is not None:
        return existing.with_inbound_message(
            visitor_message.body or "",
            channel=getattr(conversation, "channel_key", None) or existing.channel,
            country_code=getattr(ticket, "country_code", None) or existing.country_code,
        )
    return CaseContext(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        channel=getattr(conversation, "channel_key", None) or "webchat",
        country_code=getattr(ticket, "country_code", None),
        issue_type=getattr(ticket, "case_type", None) or getattr(conversation, "last_intent", None),
    ).with_inbound_message(
        visitor_message.body or "",
        channel=getattr(conversation, "channel_key", None) or "webchat",
        country_code=getattr(ticket, "country_code", None),
    )


def _ai_attempt_count(db: Session, *, conversation: WebchatConversation) -> int:
    return int(db.query(WebchatAITurn.id).filter(WebchatAITurn.conversation_id == conversation.id).count())


def _has_configured_escalation_intent(
    db: Session,
    *,
    conversation: WebchatConversation,
    ticket: Ticket,
    inbound_message: str | None,
    ai_attempt_count: int,
) -> bool:
    """Check persisted policy patterns without making the legacy term list authoritative."""

    country_code = (getattr(ticket, "country_code", None) or "GLOBAL").upper()
    channel = getattr(conversation, "channel_key", None) or "webchat"
    try:
        policies = load_escalation_policies(db, country_code=country_code, channel=channel)
        if not policies:
            return False
        return evaluate_escalation(
            inbound_message or "",
            ai_attempt_count=ai_attempt_count,
            policies=policies,
        ).matched
    except Exception as exc:
        LOGGER.warning(
            "webchat_osr_configured_escalation_prefilter_failed_non_blocking",
            extra={
                "event_payload": {
                    "conversation_id": conversation.id,
                    "ticket_id": ticket.id,
                    "error_type": type(exc).__name__,
                }
            },
        )
        return False


def _safe_escalation_payload(result: EscalationOrchestrationResult) -> dict[str, Any]:
    return {
        "action": _status_value(result.action),
        "audit_id": result.audit_id,
        "handoff_request_id": result.handoff_request.id if result.handoff_request else None,
        "ticket_id": result.ticket.id if result.ticket else None,
        "ticket_created": result.ticket_result.created if result.ticket_result else None,
        "human_status": _status_value(result.human_availability.status),
        "human_reason": result.human_availability.reason,
        "risk_key": result.escalation.risk_key,
        "escalation_action": _status_value(result.escalation.action),
        "queue_key": result.human_availability.queue_key,
        "queue_resolution": result.queue_resolution.as_safe_dict() if result.queue_resolution else None,
    }


def _result_from_osr_escalation(result: EscalationOrchestrationResult) -> dict[str, Any] | None:
    payload = _safe_escalation_payload(result)
    if result.action == EscalationOrchestrationAction.CONTINUE_AI:
        if not result.escalation.matched:
            return None
        return {
            "status": "continue_ai",
            "reason": "osr_escalation_policy_allows_ai_attempt",
            "reply_source": "nexus_osr",
            "osr_escalation": payload,
        }
    if result.action == EscalationOrchestrationAction.REQUEST_HANDOFF:
        return {
            "status": "review_required",
            "reason": "osr_handoff_requested",
            "reply_source": "nexus_osr",
            "fallback_reason": "osr_handoff_requested",
            "runtime_handoff_required": True,
            "osr_turn_closed_by_handoff": True,
            "osr_escalation": payload,
        }
    return {
        "status": "review_required",
        "reason": "osr_ticket_created",
        "reply_source": "nexus_osr",
        "fallback_reason": "osr_ticket_created",
        "runtime_handoff_required": False,
        "osr_escalation": payload,
    }


def _maybe_orchestrate_osr_escalation(
    db: Session,
    *,
    conversation: WebchatConversation,
    ticket: Ticket,
    visitor_message: WebchatMessage,
    turn: WebchatAITurn | None,
    ai_attempt_count: int | None = None,
    configured_escalation_intent: bool | None = None,
) -> dict[str, Any] | None:
    if not _osr_escalation_orchestration_enabled():
        return None
    resolved_attempt_count = ai_attempt_count if ai_attempt_count is not None else _ai_attempt_count(db, conversation=conversation)
    configured_match = configured_escalation_intent
    if configured_match is None:
        configured_match = _has_configured_escalation_intent(
            db,
            conversation=conversation,
            ticket=ticket,
            inbound_message=visitor_message.body,
            ai_attempt_count=resolved_attempt_count,
        )
    if not (
        _has_high_risk_intent(visitor_message.body)
        or _has_customer_wait_intent(visitor_message.body)
        or configured_match
    ):
        return None
    try:
        result = evaluate_escalation_for_case(
            db,
            ticket=ticket,
            conversation=conversation,
            case_context=_case_context_for_webchat(db, conversation=conversation, ticket=ticket, visitor_message=visitor_message),
            inbound_message=visitor_message.body or "",
            country_code=getattr(ticket, "country_code", None),
            channel=getattr(conversation, "channel_key", None) or "webchat",
            language=None,
            issue_type=getattr(ticket, "case_type", None) or getattr(conversation, "last_intent", None),
            tenant_id=getattr(conversation, "tenant_key", None) or "default",
            ai_attempt_count=resolved_attempt_count,
            trigger_message_id=visitor_message.id,
            ai_turn_id=turn.id if turn else None,
        )
    except Exception as exc:
        LOGGER.warning(
            "webchat_osr_escalation_failed_non_blocking",
            extra={"event_payload": {"conversation_id": conversation.id, "ticket_id": ticket.id, "visitor_message_id": visitor_message.id, "ai_turn_id": turn.id if turn else None, "error_type": type(exc).__name__}},
        )
        return None
    return _result_from_osr_escalation(result)


def process_webchat_ai_reply_job(db: Session, *, conversation_id: int, ticket_id: int, visitor_message_id: int) -> dict[str, Any]:
    conversation, ticket, visitor_message = _load_context(db, conversation_id=conversation_id, ticket_id=ticket_id, visitor_message_id=visitor_message_id)
    turn = _open_turn_for_message(db, conversation=conversation, visitor_message=visitor_message)
    if is_ai_suspended_for_handoff(conversation):
        cancel_open_ai_turns_for_handoff(db, conversation=conversation, actor_id=None, reason_code="handoff_ai_suspended_before_safe_worker")
        return {"status": "skipped", "reason": "handoff_ai_suspended", "reply_source": "suppressed"}
    if turn is not None and turn.status == "queued":
        mark_ai_turn_processing(db, conversation=conversation, turn=turn)
        cutoff_id = latest_visitor_message_id(db, conversation_id=conversation.id)
        mark_ai_turn_bridge_calling(db, conversation=conversation, turn=turn, context_cutoff_message_id=cutoff_id)
    if _agent_reply_exists(db, conversation=conversation, visitor_message=visitor_message):
        result = {"status": "skipped", "reason": "agent_reply_already_exists", "reply_source": "existing_reply"}
        _complete_turn_if_present(db, conversation=conversation, ticket=ticket, visitor_message=visitor_message, turn=turn, result=result)
        return result

    if suppress_stale_reply_if_needed(db, conversation=conversation, turn=turn, reason="newer_message_before_reply"):
        return {"status": "superseded", "reason": "newer_message_before_reply", "reply_source": "suppressed"}

    mode = (settings.webchat_ai_auto_reply_mode or "safe_ai").lower()
    if mode == "off":
        db.add(TicketEvent(
            ticket_id=ticket.id,
            actor_id=None,
            event_type=EventType.internal_note_added,
            note="Webchat AI auto reply skipped because WEBCHAT_AI_AUTO_REPLY_MODE=off",
            payload_json=json.dumps({"conversation_id": conversation.id, "visitor_message_id": visitor_message.id, "ai_turn_id": turn.id if turn else None}, ensure_ascii=False),
        ))
        result = {"status": "skipped", "reason": "webchat_ai_auto_reply_off", "reply_source": "off"}
        _complete_turn_if_present(db, conversation=conversation, ticket=ticket, visitor_message=visitor_message, turn=turn, result=result)
        return result

    high_risk_intent = _has_high_risk_intent(visitor_message.body)
    osr_enabled = _osr_escalation_orchestration_enabled()
    osr_customer_wait_intent = osr_enabled and _has_customer_wait_intent(visitor_message.body)
    osr_ai_attempt_count = _ai_attempt_count(db, conversation=conversation) if osr_enabled else 0
    configured_escalation_intent = osr_enabled and _has_configured_escalation_intent(
        db,
        conversation=conversation,
        ticket=ticket,
        inbound_message=visitor_message.body,
        ai_attempt_count=osr_ai_attempt_count,
    )
    osr_escalation_intent = osr_enabled and (
        high_risk_intent or osr_customer_wait_intent or configured_escalation_intent
    )
    if mode == "safe_ai" and (high_risk_intent or osr_escalation_intent):
        osr_escalation_result = None
        if osr_escalation_intent:
            osr_escalation_result = _maybe_orchestrate_osr_escalation(
                db,
                conversation=conversation,
                ticket=ticket,
                visitor_message=visitor_message,
                turn=turn,
                ai_attempt_count=osr_ai_attempt_count,
                configured_escalation_intent=configured_escalation_intent,
            )

        osr_continue_ai = bool(osr_escalation_result and osr_escalation_result.get("status") == "continue_ai")
        if osr_escalation_result is not None and not osr_continue_ai:
            if not osr_escalation_result.get("osr_turn_closed_by_handoff"):
                _complete_turn_if_present(
                    db,
                    conversation=conversation,
                    ticket=ticket,
                    visitor_message=visitor_message,
                    turn=turn,
                    result=osr_escalation_result,
                    audit_after_complete=False,
                )
            return osr_escalation_result

        if osr_escalation_intent and osr_escalation_result is None:
            result = _require_operator_review(
                db,
                conversation=conversation,
                ticket=ticket,
                visitor_message=visitor_message,
                reason="osr_escalation_evaluation_failed",
                turn=turn,
            )
            _complete_turn_if_present(
                db,
                conversation=conversation,
                ticket=ticket,
                visitor_message=visitor_message,
                turn=turn,
                result=result,
                audit_after_complete=False,
            )
            return result

        if high_risk_intent and not osr_continue_ai:
            result = _require_operator_review(db, conversation=conversation, ticket=ticket, visitor_message=visitor_message, reason="webchat_safe_ai_high_risk_review", turn=turn)
            _complete_turn_if_present(
                db,
                conversation=conversation,
                ticket=ticket,
                visitor_message=visitor_message,
                turn=turn,
                result=result,
                audit_after_complete=not osr_enabled,
            )
            return result

    result = _legacy_process_webchat_ai_reply_job(db, conversation_id=conversation_id, ticket_id=ticket_id, visitor_message_id=visitor_message_id, ai_turn_id=turn.id if turn else None)
    _complete_turn_if_present(db, conversation=conversation, ticket=ticket, visitor_message=visitor_message, turn=turn, result=result or {})
    return result
