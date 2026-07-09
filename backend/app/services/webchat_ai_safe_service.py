from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from ..enums import EventType
from ..models import Ticket, TicketEvent
from ..settings import get_settings
from ..webchat_models import WebchatAITurn, WebchatConversation, WebchatMessage
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

HIGH_RISK_TERMS = (
    "refund", "compensation", "lost", "damaged", "customs", " tax ", "claim", "legal", "pod",
    "proof of delivery", "delivered but not received", "address change", "change address", "complaint",
    "赔偿", "赔付", "退款", "丢件", "破损", "海关", "清关", "签收未收到", "改地址", "投诉", "索赔",
)


def _has_high_risk_intent(text: str | None) -> bool:
    normalized = f" {(text or '').lower()} "
    return any(term.lower() in normalized for term in HIGH_RISK_TERMS)


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


def _complete_turn_if_present(db: Session, *, conversation: WebchatConversation, ticket: Ticket, visitor_message: WebchatMessage, turn: WebchatAITurn | None, result: dict[str, Any]) -> None:
    if turn is None:
        return
    complete_ai_turn_with_reply(db, conversation=conversation, turn=turn, result=result)
    try:
        with db.begin_nested():
            audit_completed_webchat_ai_turn(
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
            extra={"event_payload": {"conversation_id": conversation.id, "ticket_id": ticket.id, "visitor_message_id": visitor_message.id, "ai_turn_id": turn.id, "error_type": type(exc).__name__}},
        )


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

    if mode == "safe_ai" and _has_high_risk_intent(visitor_message.body):
        result = _require_operator_review(db, conversation=conversation, ticket=ticket, visitor_message=visitor_message, reason="webchat_safe_ai_high_risk_review", turn=turn)
        _complete_turn_if_present(db, conversation=conversation, ticket=ticket, visitor_message=visitor_message, turn=turn, result=result)
        return result

    result = _legacy_process_webchat_ai_reply_job(db, conversation_id=conversation_id, ticket_id=ticket_id, visitor_message_id=visitor_message_id, ai_turn_id=turn.id if turn else None)
    _complete_turn_if_present(db, conversation=conversation, ticket=ticket, visitor_message=visitor_message, turn=turn, result=result or {})
    return result
