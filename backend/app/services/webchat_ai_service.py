from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import asdict, replace
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from ..enums import ConversationState, EventType, MessageStatus, SourceChannel, TicketStatus
from ..models import Ticket, TicketEvent
from ..settings import get_settings
from ..utils.time import ensure_utc, utc_now
from ..webchat_models import WebchatAITurn, WebchatConversation, WebchatMessage
from .customer_language import detect_customer_language
from .customer_visible_message_service import create_customer_visible_message
from .outbound_safety import evaluate_outbound_safety, format_safety_reasons
from .background_jobs import enqueue_speedaf_work_order_create_job
from .sla_service import evaluate_sla, update_first_response
from .speedaf.redactor import safe_caller_payload, safe_waybill_payload
from .speedaf.status_map import is_auto_work_order_type_allowed
from .tracking_fact_schema import TrackingFactResult
from .tracking_fact_service import extract_tracking_number, lookup_tracking_fact
from .webchat_ai_decision_runtime.tool_registry import canonical_tool_name
from .webchat_ai_turn_service import is_ai_suspended_for_handoff, safe_write_webchat_event, suppress_stale_reply_if_needed
from .webchat_fact_gate import evaluate_webchat_fact_gate
from .webchat_handoff_service import request_webchat_handoff
from .webchat_runtime_ai_service import WebchatRuntimeReplyResult, generate_webchat_runtime_reply
from .ai_runtime_context import build_webchat_runtime_context
from .ai_reply_contract import build_ai_reply_contract

LOGGER = logging.getLogger("nexusdesk")
settings = get_settings()
_LAST_AI_REPLY_SOURCE = "private_ai_runtime"
_LAST_AI_FALLBACK_REASON = None
_LAST_BRIDGE_ELAPSED_MS = None
_LAST_BRIDGE_EFFECTIVE_TIMEOUT_SECONDS = None
_LAST_BRIDGE_WAIT_TIMEOUT_MS = None
_LAST_RUNTIME_HANDOFF_REQUIRED = False
_LAST_RUNTIME_HANDOFF_REASON = None
_LAST_RUNTIME_RECOMMENDED_AGENT_ACTION = None
_LAST_RUNTIME_TRACE = None
_LAST_RUNTIME_RAG_TRACE = None
_LAST_RUNTIME_TOOL_CALLS = None

AI_AUTHOR_LABEL = "AI Assistant"
MAX_HISTORY_MESSAGES = 12

def _is_whatsapp_conversation(conversation: WebchatConversation) -> bool:
    return str(getattr(conversation, "channel_key", "") or "").lower() == SourceChannel.whatsapp.value


def _mark_ai_review_required(
    db: Session,
    *,
    conversation: WebchatConversation,
    ticket: Ticket,
    visitor_message: WebchatMessage,
    reason: str,
    turn: WebchatAITurn | None = None,
    reply_source: str | None = None,
    runtime_trace: dict[str, Any] | None = None,
    bridge_elapsed_ms: int | None = None,
    bridge_effective_timeout_seconds: int | None = None,
    bridge_wait_timeout_ms: int | None = None,
) -> dict[str, Any]:
    now = utc_now()
    reason = (reason or "ai_failed_no_public_reply")[:240]
    # A failed/suppressed AI turn is not a handoff. Keep the conversation AI-active
    # so the next customer message can retry the unified runtime instead of going silent.
    ticket.updated_at = now
    conversation.updated_at = now
    db.add(TicketEvent(
        ticket_id=ticket.id,
        actor_id=None,
        event_type=EventType.internal_note_added,
        note="AI reply suppressed without customer-visible fallback",
        payload_json=json.dumps({
            "conversation_id": conversation.id,
            "public_conversation_id": conversation.public_id,
            "visitor_message_id": visitor_message.id,
            "ai_turn_id": turn.id if turn else None,
            "reply_source": reply_source,
            "reason": reason,
            "external_send": False,
            "customer_visible_reply": False,
            "runtime_trace": runtime_trace,
            "bridge_elapsed_ms": bridge_elapsed_ms,
        }, ensure_ascii=False),
    ))
    safe_write_webchat_event(
        db,
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        event_type="ai_turn.review_required",
        payload={
            "ai_turn_id": turn.id if turn else None,
            "visitor_message_id": visitor_message.id,
            "reply_source": reply_source,
            "reason": reason,
            "customer_visible_reply": False,
            "runtime_trace": runtime_trace,
            "bridge_elapsed_ms": bridge_elapsed_ms,
        },
    )
    db.flush()
    return {
        "status": "review_required",
        "reason": reason,
        "reply_source": reply_source or "suppressed",
        "fallback_reason": reason,
        "message_id": None,
        "bridge_elapsed_ms": bridge_elapsed_ms,
        "bridge_effective_timeout_seconds": bridge_effective_timeout_seconds,
        "bridge_wait_timeout_ms": bridge_wait_timeout_ms,
        "runtime_trace": runtime_trace,
    }


def _webchat_session_policy(conversation: WebchatConversation, history_rows: list[WebchatMessage], total_messages: int) -> dict[str, Any]:
    ttl_hours = max(1, int(getattr(settings, 'webchat_ai_session_ttl_hours', 24) or 24))
    max_messages = max(4, int(getattr(settings, 'webchat_ai_session_max_messages', 40) or 40))
    summary_messages = max(1, int(getattr(settings, 'webchat_ai_session_summary_messages', 8) or 8))
    base_key = f"webchat:{conversation.tenant_key}:{conversation.channel_key}:{conversation.public_id}"

    rotation_by_messages = total_messages // max_messages
    created_at = ensure_utc(conversation.created_at) or utc_now()
    age = utc_now() - created_at
    rotation_by_ttl = int(age // timedelta(hours=ttl_hours)) if ttl_hours > 0 else 0
    generation = max(rotation_by_messages, rotation_by_ttl)
    session_key = base_key if generation <= 0 else f"{base_key}:g{generation}"

    summary = None
    if generation > 0 and history_rows:
        older_rows = history_rows[:-summary_messages] if len(history_rows) > summary_messages else history_rows[:-1]
        older_rows = older_rows[-summary_messages:]
        if older_rows:
            summary_lines = []
            for row in older_rows:
                speaker = 'Visitor' if row.direction == 'visitor' else 'Agent'
                text = (row.body_text or row.body or '').strip().replace('\n', ' ')
                if text:
                    summary_lines.append(f"- {speaker}: {text[:160]}")
            if summary_lines:
                summary = "Prior conversation summary:\n" + "\n".join(summary_lines)

    return {
        'session_key': session_key,
        'base_key': base_key,
        'generation': generation,
        'ttl_hours': ttl_hours,
        'max_messages': max_messages,
        'summary': summary,
        'rotation_reason': 'ttl_or_message_limit' if generation > 0 else None,
    }


_AUTO_WORK_ORDER_INTENT_RE = re.compile(
    r"\b(chase|expedite|urge|follow\s*up|delivery\s*follow[- ]?up|open\s+(?:a\s+)?case|create\s+(?:a\s+)?(?:case|ticket|work\s*order)|"
    r"not\s+received|haven't\s+received|have\s+not\s+received|still\s+not|too\s+slow|delayed|late)\b|"
    r"催派|催一下|催件|催促|催办|加急|工单|建单|创建工单|没收到|未收到|还没收到|太慢|延误|派送慢|投诉",
    re.IGNORECASE,
)


def _env_enabled(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _phone_candidate(*values: str | None) -> str | None:
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        match = re.search(r"\+?\d[\d\s().-]{6,}\d", text)
        if not match:
            continue
        cleaned = re.sub(r"[\s().-]+", "", match.group(0))
        if 8 <= len(re.sub(r"\D", "", cleaned)) <= 18:
            return cleaned[:80]
    return None


def _runtime_requested_work_order(tool_calls: list[dict[str, Any]] | None) -> tuple[bool, dict[str, Any]]:
    for call in tool_calls or []:
        if not isinstance(call, dict):
            continue
        tool_name = canonical_tool_name(call.get("tool_name") or call.get("name") or call.get("tool"))
        if tool_name != "speedaf.workOrder.create":
            continue
        args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
        return True, dict(args)
    return False, {}


def _auto_work_order_description(*, visitor_message: WebchatMessage, tracking_fact: TrackingFactResult, tool_args: dict[str, Any]) -> str:
    requested = " ".join(str(tool_args.get(key) or "").strip() for key in ("description", "summary", "reason") if tool_args.get(key)).strip()
    customer_text = " ".join(str(visitor_message.body or "").strip().split())[:90]
    status = tracking_fact.status_label or tracking_fact.status or "verified tracking fact"
    base = requested or f"Customer requested delivery follow-up. Latest message: {customer_text}"
    return f"{base} Current status: {status}"[:200]


def _maybe_enqueue_auto_speedaf_work_order(
    db: Session,
    *,
    conversation: WebchatConversation,
    ticket: Ticket,
    visitor_message: WebchatMessage,
    tracking_fact: TrackingFactResult | None,
    runtime_tool_calls: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    if not _env_enabled("WEBCHAT_AI_AUTO_WORK_ORDER_ENABLED", False):
        return None
    if not _env_enabled("SPEEDAF_WORK_ORDER_CREATE_ENABLED", False):
        return {"status": "skipped", "reason": "speedaf_work_order_create_disabled"}
    if not (tracking_fact and tracking_fact.fact_evidence_present and tracking_fact.pii_redacted and tracking_fact.tracking_number):
        return {"status": "skipped", "reason": "trusted_tracking_fact_required"}

    requested_by_runtime, tool_args = _runtime_requested_work_order(runtime_tool_calls)
    requested_by_customer = bool(_AUTO_WORK_ORDER_INTENT_RE.search(visitor_message.body or ""))
    if not (requested_by_runtime or requested_by_customer):
        return None

    work_order_type = str(tool_args.get("workOrderType") or tool_args.get("work_order_type") or "WT0103-05").strip()
    if not is_auto_work_order_type_allowed(work_order_type):
        return {"status": "skipped", "reason": "work_order_type_not_allowed", "workOrderType": work_order_type}

    caller_id = _phone_candidate(
        getattr(conversation, "visitor_phone", None),
        getattr(ticket, "preferred_reply_contact", None),
        getattr(getattr(ticket, "customer", None), "phone", None),
        getattr(conversation, "visitor_name", None),
    )
    if not caller_id:
        return {"status": "skipped", "reason": "caller_id_required"}

    waybill_code = str(tracking_fact.tracking_number).strip().upper()
    description = _auto_work_order_description(visitor_message=visitor_message, tracking_fact=tracking_fact, tool_args=tool_args)
    job = enqueue_speedaf_work_order_create_job(
        db,
        ticket_id=ticket.id,
        conversation_id=conversation.id,
        waybill_code=waybill_code,
        caller_id=caller_id,
        description=description,
        work_order_type=work_order_type,
    )
    payload = {
        "job_id": job.id,
        "dedupe_key": job.dedupe_key,
        "workOrderType": work_order_type,
        "trigger": "runtime_tool_call" if requested_by_runtime else "customer_delivery_followup_intent",
        **safe_waybill_payload(waybill_code),
        **safe_caller_payload(caller_id),
    }
    db.add(TicketEvent(
        ticket_id=ticket.id,
        actor_id=None,
        event_type=EventType.field_updated,
        field_name="speedaf_work_order",
        new_value="queued",
        note="Speedaf delivery follow-up work order queued by AI policy.",
        payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
    ))
    safe_write_webchat_event(
        db,
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        event_type="speedaf.work_order.auto_queued",
        payload=payload,
    )
    return {"status": "queued", **payload}


def _message_metadata(*, generated_by: str, decision_level: str, fallback_reason: str | None, reply_source: str | None, fact_evidence_present: bool = False, **extra: Any) -> str:
    payload = {
        "generated_by": generated_by,
        "intent": None,
        "confidence": None,
        "safety_level": decision_level,
        "fallback_reason": fallback_reason,
        "fact_evidence_present": fact_evidence_present,
        "external_send": False,
        "reply_source": reply_source,
    }
    payload.update({key: value for key, value in extra.items() if value is not None})
    return json.dumps(payload, ensure_ascii=False)


def process_webchat_ai_reply_job(
    db: Session,
    *,
    conversation_id: int,
    ticket_id: int,
    visitor_message_id: int,
    ai_turn_id: int | None = None,
) -> dict[str, Any]:
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
        raise RuntimeError(
            "webchat job payload mismatch: "
            f"conversation_id={conversation_id} ticket_id={ticket_id} visitor_message_id={visitor_message_id}"
        )

    turn = db.query(WebchatAITurn).filter(WebchatAITurn.id == ai_turn_id, WebchatAITurn.conversation_id == conversation.id).first() if ai_turn_id else None
    if is_ai_suspended_for_handoff(conversation):
        return {"status": "skipped", "reason": "handoff_ai_suspended", "reply_source": "suppressed"}

    existing_agent = (
        db.query(WebchatMessage.id)
        .filter(
            WebchatMessage.conversation_id == conversation.id,
            WebchatMessage.direction == "agent",
            WebchatMessage.id > visitor_message.id,
            WebchatMessage.author_label == AI_AUTHOR_LABEL,
        )
        .first()
    )
    if existing_agent:
        return {"status": "skipped", "reason": "agent_reply_already_exists", "reply_source": "existing_reply"}

    if suppress_stale_reply_if_needed(db, conversation=conversation, turn=turn, reason="newer_message_before_bridge_reply"):
        LOGGER.info(
            "webchat_ai_reply_suppressed_stale",
            extra={"event_payload": {"conversation_id": conversation.id, "ticket_id": ticket.id, "visitor_message_id": visitor_message.id, "ai_turn_id": ai_turn_id, "reason": "newer_message_before_bridge_reply"}},
        )
        return {"status": "superseded", "reason": "newer_message_before_bridge_reply", "reply_source": "suppressed"}

    total_messages = (
        db.query(WebchatMessage.id)
        .filter(WebchatMessage.conversation_id == conversation.id)
        .count()
    )

    history_rows = (
        db.query(WebchatMessage)
        .filter(WebchatMessage.conversation_id == conversation.id)
        .order_by(WebchatMessage.created_at.desc(), WebchatMessage.id.desc())
        .limit(MAX_HISTORY_MESSAGES)
        .all()
    )
    history_rows.reverse()

    tracking_fact = _maybe_lookup_tracking_fact(
        conversation=conversation,
        ticket=ticket,
        visitor_message=visitor_message,
        history_rows=history_rows,
    )
    fact_evidence_present = bool(tracking_fact and tracking_fact.fact_evidence_present and tracking_fact.pii_redacted)
    tracking_fact_metadata = tracking_fact.metadata_payload() if tracking_fact else {}
    tracking_fact_metadata.pop("fact_evidence_present", None)

    session_policy = _webchat_session_policy(conversation, history_rows, total_messages)
    ai_reply = _generate_ai_reply(ticket=ticket, conversation=conversation, visitor_message=visitor_message, history_rows=history_rows, tracking_fact=tracking_fact, session_policy=session_policy)
    reply_source = _LAST_AI_REPLY_SOURCE
    fallback_reason = _LAST_AI_FALLBACK_REASON
    bridge_elapsed_ms = _LAST_BRIDGE_ELAPSED_MS
    bridge_timeout_seconds = getattr(settings, "external_channel_bridge_timeout_seconds", None)
    bridge_effective_timeout_seconds = _LAST_BRIDGE_EFFECTIVE_TIMEOUT_SECONDS
    bridge_wait_timeout_ms = _LAST_BRIDGE_WAIT_TIMEOUT_MS
    runtime_handoff_required = bool(_LAST_RUNTIME_HANDOFF_REQUIRED)
    runtime_handoff_reason = _LAST_RUNTIME_HANDOFF_REASON
    runtime_recommended_agent_action = _LAST_RUNTIME_RECOMMENDED_AGENT_ACTION
    runtime_trace = _LAST_RUNTIME_TRACE if isinstance(_LAST_RUNTIME_TRACE, dict) else None
    rag_trace = _LAST_RUNTIME_RAG_TRACE if isinstance(_LAST_RUNTIME_RAG_TRACE, dict) else None
    runtime_tool_calls = _LAST_RUNTIME_TOOL_CALLS if isinstance(_LAST_RUNTIME_TOOL_CALLS, list) else []
    sanitized_empty = False
    fact_gate_reason = None
    if not ai_reply:
        fallback_reason = fallback_reason or "empty_ai_reply"
        return _mark_ai_review_required(
            db,
            conversation=conversation,
            ticket=ticket,
            visitor_message=visitor_message,
            reason=fallback_reason,
            turn=turn,
            reply_source=reply_source,
            runtime_trace=runtime_trace,
            bridge_elapsed_ms=bridge_elapsed_ms,
            bridge_effective_timeout_seconds=bridge_effective_timeout_seconds,
            bridge_wait_timeout_ms=bridge_wait_timeout_ms,
        )

    ai_reply = _sanitize_public_ai_reply(ai_reply)
    grounding_applied = False
    grounding_source = None

    if not ai_reply.strip():
        fallback_reason = fallback_reason or "sanitizer_empty"
        sanitized_empty = True
        return _mark_ai_review_required(
            db,
            conversation=conversation,
            ticket=ticket,
            visitor_message=visitor_message,
            reason=fallback_reason,
            turn=turn,
            reply_source=reply_source,
            runtime_trace=runtime_trace,
            bridge_elapsed_ms=bridge_elapsed_ms,
            bridge_effective_timeout_seconds=bridge_effective_timeout_seconds,
            bridge_wait_timeout_ms=bridge_wait_timeout_ms,
        )

    decision = evaluate_outbound_safety(ticket, ai_reply, source="webchat_ai", has_fact_evidence=fact_evidence_present)
    final_body = decision.normalized_body
    safety_payload = asdict(decision)

    if decision.level != "allow" or decision.requires_human_review:
        fallback_reason = fallback_reason or format_safety_reasons(decision)
        return _mark_ai_review_required(
            db,
            conversation=conversation,
            ticket=ticket,
            visitor_message=visitor_message,
            reason=fallback_reason,
            turn=turn,
            reply_source=reply_source,
            runtime_trace=runtime_trace,
            bridge_elapsed_ms=bridge_elapsed_ms,
            bridge_effective_timeout_seconds=bridge_effective_timeout_seconds,
            bridge_wait_timeout_ms=bridge_wait_timeout_ms,
        )

    fact_decision = evaluate_webchat_fact_gate(
        final_body,
        fact_evidence_present=fact_evidence_present,
        allow_tracking_status_card=bool(getattr(settings, "webchat_tracking_fact_card_enabled", False)),
    )
    if not fact_decision.allowed:
        fact_gate_reason = fact_decision.reason or "fact_gate_blocked"
        fallback_reason = fallback_reason or fact_gate_reason
        LOGGER.info(
            "webchat_fact_gate_blocked",
            extra={"event_payload": {
                "conversation_id": conversation.id,
                "ticket_id": ticket.id,
                "visitor_message_id": visitor_message.id,
                "ai_turn_id": ai_turn_id,
                "reason": fact_gate_reason,
            }},
        )
        return _mark_ai_review_required(
            db,
            conversation=conversation,
            ticket=ticket,
            visitor_message=visitor_message,
            reason=fallback_reason,
            turn=turn,
            reply_source=reply_source,
            runtime_trace=runtime_trace,
            bridge_elapsed_ms=bridge_elapsed_ms,
            bridge_effective_timeout_seconds=bridge_effective_timeout_seconds,
            bridge_wait_timeout_ms=bridge_wait_timeout_ms,
        )

    if suppress_stale_reply_if_needed(db, conversation=conversation, turn=turn, reason="newer_message_before_reply_commit"):
        LOGGER.info(
            "webchat_ai_reply_suppressed_stale",
            extra={"event_payload": {"conversation_id": conversation.id, "ticket_id": ticket.id, "visitor_message_id": visitor_message.id, "ai_turn_id": ai_turn_id, "reason": "newer_message_before_reply_commit"}},
        )
        return {"status": "superseded", "reason": "newer_message_before_reply_commit", "reply_source": "suppressed"}

    auto_work_order = _maybe_enqueue_auto_speedaf_work_order(
        db,
        conversation=conversation,
        ticket=ticket,
        visitor_message=visitor_message,
        tracking_fact=tracking_fact,
        runtime_tool_calls=runtime_tool_calls,
    )

    is_external_whatsapp = _is_whatsapp_conversation(conversation)
    delivery_status = "queued" if is_external_whatsapp else "sent"
    provider_status = "whatsapp_ai_reply_queued" if is_external_whatsapp else "webchat_ai_delivered"
    if ticket.conversation_state not in {ConversationState.human_review_required, ConversationState.ready_to_reply}:
        ticket.conversation_state = ConversationState.ai_active

    reply_channel = SourceChannel.whatsapp if is_external_whatsapp else SourceChannel.web_chat
    outbound_event_type = EventType.outbound_queued if is_external_whatsapp else EventType.outbound_sent
    outbound_event_note = "WhatsApp AI reply queued" if is_external_whatsapp else "Webchat AI reply sent"
    ai_contract = build_ai_reply_contract(
        body=final_body,
        runtime_trace=runtime_trace,
        safety_status="passed" if decision.level in {"allow", "ok", "pass"} else "reviewed",
    )

    if runtime_handoff_required:
        ticket.required_action = runtime_recommended_agent_action or "Runtime requested human review"
        ticket.conversation_state = ConversationState.human_review_required
        request_webchat_handoff(
            db,
            conversation=conversation,
            ticket=ticket,
            source="ai_runtime",
            trigger_type="runtime_handoff",
            reason_code=(runtime_handoff_reason or "ai_runtime_requested_handoff")[:120],
            reason_text=runtime_handoff_reason,
            recommended_agent_action=runtime_recommended_agent_action,
            trigger_message_id=visitor_message.id,
            requested_by_actor_type="ai_runtime",
        )

    update_first_response(ticket)
    if not runtime_handoff_required:
        ticket.status = TicketStatus.waiting_customer
        ticket.conversation_state = ConversationState.waiting_customer
    ticket.last_ai_update = final_body
    ticket.last_runtime_reply_at = utc_now()
    ticket.updated_at = utc_now()
    conversation.updated_at = utc_now()
    conversation.last_seen_at = utc_now()

    event_payload = {
        "public_conversation_id": conversation.public_id,
        "conversation_id": conversation.id,
        "ticket_id": ticket.id,
        "visitor_message_id": visitor_message.id,
        "ai_turn_id": ai_turn_id,
        "safety": safety_payload,
        "fallback_reason": fallback_reason,
        "fact_gate_reason": fact_gate_reason,
        "fact_evidence_present": fact_evidence_present,
        "tracking_fact": tracking_fact_metadata or None,
        "reply_source": reply_source,
        "provider_status": provider_status,
        "external_send": is_external_whatsapp,
        "reply_channel": SourceChannel.whatsapp.value if is_external_whatsapp else SourceChannel.web_chat.value,
        "external_channel_session_key": session_policy['session_key'],
        "external_channel_session_generation": session_policy['generation'],
        "external_channel_session_rotation_reason": session_policy['rotation_reason'],
        "bridge_elapsed_ms": bridge_elapsed_ms,
        "bridge_timeout_seconds": bridge_timeout_seconds,
        "bridge_effective_timeout_seconds": bridge_effective_timeout_seconds,
        "bridge_wait_timeout_ms": bridge_wait_timeout_ms,
        "runtime_trace": runtime_trace,
        "runtime_tool_calls": runtime_tool_calls,
        "auto_work_order": auto_work_order,
        "sanitized_empty": sanitized_empty,
        "rag_trace": rag_trace,
        "grounding_applied": grounding_applied,
        "grounding_source": grounding_source,
    }
    visible_result = create_customer_visible_message(
        db,
        ticket=ticket,
        conversation=conversation,
        channel=reply_channel,
        body=final_body,
        origin="provider_runtime",
        created_by=None,
        provider_status=provider_status,
        ai_contract=ai_contract,
        outbound_status=MessageStatus.sent if not is_external_whatsapp else None,
        ai_turn_id=ai_turn_id,
        delivery_status=delivery_status,
        metadata_json=_message_metadata(
            generated_by="webchat_ai",
            decision_level=decision.level,
            fallback_reason=fallback_reason,
            reply_source=reply_source,
            fact_evidence_present=fact_evidence_present,
            external_send=is_external_whatsapp,
            external_channel_session_key=session_policy['session_key'],
            external_channel_session_generation=session_policy['generation'],
            external_channel_session_rotation_reason=session_policy['rotation_reason'],
            fact_gate_reason=fact_gate_reason,
            bridge_elapsed_ms=bridge_elapsed_ms,
            bridge_timeout_seconds=bridge_timeout_seconds,
            bridge_effective_timeout_seconds=bridge_effective_timeout_seconds,
            bridge_wait_timeout_ms=bridge_wait_timeout_ms,
            runtime_trace=runtime_trace,
            runtime_tool_calls=runtime_tool_calls,
            auto_work_order=auto_work_order,
            sanitized_empty=sanitized_empty,
            ai_turn_id=ai_turn_id,
            rag_trace=rag_trace,
            grounding_applied=grounding_applied,
            grounding_source=grounding_source,
            **tracking_fact_metadata,
        ),
        author_label=AI_AUTHOR_LABEL,
        safety_level=decision.level,
        safety_reasons_json=json.dumps(safety_payload.get("reasons", []), ensure_ascii=False),
        event_type=outbound_event_type,
        event_note=outbound_event_note,
        event_payload=event_payload,
    )
    message = visible_result.webchat_message
    outbound_message = visible_result.outbound_message
    if message is None or outbound_message is None:
        return {"status": "suppressed", "reason": "no_customer_visible_message", "reply_source": reply_source}
    event_payload["webchat_message_id"] = message.id
    event_payload["outbound_message_id"] = outbound_message.id
    if tracking_fact:
        db.add(TicketEvent(
            ticket_id=ticket.id,
            actor_id=None,
            event_type=EventType.field_updated,
            note="Webchat tracking fact evaluated",
            payload_json=json.dumps({
                "event": "webchat_tracking_fact_used" if fact_evidence_present else "webchat_tracking_fact_not_used",
                "conversation_id": conversation.id,
                "ticket_id": ticket.id,
                "tool_name": tracking_fact.tool_name,
                "tool_status": tracking_fact.tool_status,
                "fact_evidence_present": fact_evidence_present,
                "pii_redacted": tracking_fact.pii_redacted,
                "checked_at": tracking_fact.checked_at,
                "external_send": is_external_whatsapp,
                "tracking_number_hash": tracking_fact_metadata.get("tracking_number_hash"),
                "failure_reason": tracking_fact.failure_reason,
            }, ensure_ascii=False),
        ))
    evaluate_sla(ticket, db)
    LOGGER.info(
        "webchat_ai_reply_sent",
        extra={"event_payload": {
            "conversation_id": conversation.id,
            "ticket_id": ticket.id,
            "visitor_message_id": visitor_message.id,
            "webchat_message_id": message.id,
            "fallback": bool(fallback_reason),
            "reply_source": reply_source,
            "fallback_reason": fallback_reason,
            "fact_gate_reason": fact_gate_reason,
            "fact_evidence_present": fact_evidence_present,
            "tracking_fact_tool_status": tracking_fact.tool_status if tracking_fact else None,
            "provider_status": provider_status,
            "external_send": is_external_whatsapp,
            "external_channel_session_key": session_policy['session_key'],
            "external_channel_session_generation": session_policy['generation'],
            "external_channel_session_rotation_reason": session_policy['rotation_reason'],
            "bridge_elapsed_ms": bridge_elapsed_ms,
            "bridge_timeout_seconds": bridge_timeout_seconds,
            "bridge_effective_timeout_seconds": bridge_effective_timeout_seconds,
            "bridge_wait_timeout_ms": bridge_wait_timeout_ms,
            "runtime_trace": runtime_trace,
            "sanitized_empty": sanitized_empty,
            "rag_trace": rag_trace,
            "grounding_applied": grounding_applied,
            "grounding_source": grounding_source,
        }},
    )
    return {"status": "done", "message_id": message.id, "fallback": False, "reply_source": reply_source, "fallback_reason": None, "fact_evidence_present": fact_evidence_present, "grounding_applied": grounding_applied, "grounding_source": grounding_source, "bridge_elapsed_ms": bridge_elapsed_ms, "bridge_effective_timeout_seconds": bridge_effective_timeout_seconds, "bridge_wait_timeout_ms": bridge_wait_timeout_ms, "runtime_trace": runtime_trace}


def _maybe_lookup_tracking_fact(*, conversation: WebchatConversation, ticket: Ticket, visitor_message: WebchatMessage, history_rows: list[WebchatMessage]) -> TrackingFactResult | None:
    if not getattr(settings, "webchat_tracking_fact_lookup_enabled", False):
        return None
    current_tracking = extract_tracking_number(visitor_message.body)
    if current_tracking:
        tracking_number = current_tracking
    elif _allows_history_tracking_lookup(visitor_message.body):
        history_candidates = [row.body for row in reversed(history_rows)]
        tracking_number = (ticket.tracking_number or "").strip() or extract_tracking_number(*history_candidates)
    else:
        return None
    if not tracking_number:
        return None
    lookup_started = time.monotonic()
    result = lookup_tracking_fact(
        tracking_number=tracking_number,
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        request_id=f"webchat-{conversation.public_id}-{visitor_message.id}",
    )
    lookup_elapsed_ms = int((time.monotonic() - lookup_started) * 1000)
    result = replace(result, lookup_elapsed_ms=lookup_elapsed_ms)
    LOGGER.info(
        "webchat_tracking_fact_lookup_result",
        extra={"event_payload": {
            "conversation_id": conversation.id,
            "ticket_id": ticket.id,
            "visitor_message_id": visitor_message.id,
            "tool_name": result.tool_name,
            "tool_status": result.tool_status,
            "fact_evidence_present": result.fact_evidence_present,
            "pii_redacted": result.pii_redacted,
            "lookup_elapsed_ms": lookup_elapsed_ms,
            "failure_reason": result.failure_reason,
        }},
    )
    return result


def _history_as_runtime_context(
    *,
    history_rows: list[WebchatMessage],
    visitor_message: WebchatMessage,
) -> list[dict[str, str]]:
    recent: list[dict[str, str]] = []
    for row in history_rows[-MAX_HISTORY_MESSAGES:]:
        if row.id == visitor_message.id:
            continue
        text = (row.body_text or row.body or "").strip()
        if not text:
            continue
        role = "customer" if row.direction == "visitor" else "ai"
        recent.append({"role": role, "text": text[:500]})
    return recent[-MAX_HISTORY_MESSAGES:]


def _run_runtime_reply_sync(**kwargs: Any) -> WebchatRuntimeReplyResult:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(generate_webchat_runtime_reply(**kwargs))
    raise RuntimeError("webchat_ai_runtime_event_loop_running")


def _language_hint(text: str | None) -> str | None:
    return detect_customer_language(text).language


_SERVICE_POLICY_MARKERS = (
    "service availability",
    "domestic-to-domestic",
    "domestic to domestic",
    "local-to-local",
    "local to local",
    "本地到本地",
    "本地寄本地",
    "瑞士本地",
    "国内件",
    "支不支持",
    "支持寄送",
    "支持配送",
    "是否开通",
    "开通了吗",
    "暂未开通",
)

_HISTORY_TRACKING_CONTEXT_MARKERS = (
    "track",
    "tracking",
    "parcel",
    "package",
    "shipment",
    "waybill",
    "delivery",
    "where is",
    "status",
    "recipient",
    "received",
    "receive",
    "not received",
    "did not receive",
    "单号",
    "运单",
    "物流",
    "快递",
    "包裹",
    "收件人",
    "没收到",
    "没有收到",
    "签收",
    "派送",
    "配送",
    "查件",
    "刚刚这个",
)


def _looks_like_service_policy_question(text: str | None) -> bool:
    lowered = str(text or "").strip().lower()
    return bool(lowered and any(marker in lowered for marker in _SERVICE_POLICY_MARKERS))


def _allows_history_tracking_lookup(text: str | None) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    if _looks_like_service_policy_question(lowered):
        return False
    return any(marker in lowered for marker in _HISTORY_TRACKING_CONTEXT_MARKERS)


def _generate_ai_reply(*, ticket: Ticket, conversation: WebchatConversation, visitor_message: WebchatMessage, history_rows: list[WebchatMessage], tracking_fact: TrackingFactResult | None = None, session_policy: dict[str, Any] | None = None) -> str:
    global _LAST_AI_REPLY_SOURCE, _LAST_AI_FALLBACK_REASON, _LAST_BRIDGE_ELAPSED_MS, _LAST_BRIDGE_EFFECTIVE_TIMEOUT_SECONDS, _LAST_BRIDGE_WAIT_TIMEOUT_MS
    global _LAST_RUNTIME_HANDOFF_REQUIRED, _LAST_RUNTIME_HANDOFF_REASON, _LAST_RUNTIME_RECOMMENDED_AGENT_ACTION, _LAST_RUNTIME_TRACE, _LAST_RUNTIME_RAG_TRACE, _LAST_RUNTIME_TOOL_CALLS

    _LAST_AI_REPLY_SOURCE = "private_ai_runtime"
    _LAST_AI_FALLBACK_REASON = None
    _LAST_BRIDGE_ELAPSED_MS = None
    _LAST_BRIDGE_EFFECTIVE_TIMEOUT_SECONDS = None
    _LAST_BRIDGE_WAIT_TIMEOUT_MS = None
    _LAST_RUNTIME_HANDOFF_REQUIRED = False
    _LAST_RUNTIME_HANDOFF_REASON = None
    _LAST_RUNTIME_RECOMMENDED_AGENT_ACTION = None
    _LAST_RUNTIME_TRACE = None
    _LAST_RUNTIME_RAG_TRACE = None
    _LAST_RUNTIME_TOOL_CALLS = None

    tracking_fact_summary = tracking_fact.prompt_summary() if tracking_fact and tracking_fact.fact_evidence_present and tracking_fact.pii_redacted else None
    tracking_fact_metadata = tracking_fact.metadata_payload() if tracking_fact else {}
    tracking_fact_metadata.pop("fact_evidence_present", None)
    target_language = _language_hint(visitor_message.body)
    tracking_number_for_context = (
        getattr(tracking_fact, "tracking_number", None)
        or tracking_fact_metadata.get("tracking_number")
        or ticket.tracking_number
    )
    runtime_context = None
    db_session = Session.object_session(ticket)
    if db_session is not None:
        try:
            runtime_context = build_webchat_runtime_context(
                db=db_session,
                tenant_key=conversation.tenant_key,
                channel_key=conversation.channel_key,
                body=visitor_message.body or "",
                market_id=getattr(ticket, "market_id", None),
                language=target_language,
                tracking_number=tracking_number_for_context,
                tracking_fact_evidence_present=bool(tracking_fact_summary),
                ticket=ticket,
                conversation=conversation,
                customer=getattr(ticket, "customer", None),
                channel_payload=tracking_fact_metadata,
            )
        except Exception:
            LOGGER.exception("webchat_ai_runtime_context_prefetch_failed", extra={"event_payload": {"conversation_id": conversation.id, "ticket_id": ticket.id}})
    try:
        result = _run_runtime_reply_sync(
            tenant_key=conversation.tenant_key,
            channel_key=conversation.channel_key,
            session_id=(session_policy or {}).get("session_key") or f"webchat:{conversation.tenant_key}:{conversation.channel_key}:{conversation.public_id}",
            body=visitor_message.body or "",
            recent_context=_history_as_runtime_context(history_rows=history_rows, visitor_message=visitor_message),
            request_id=f"webchat-ai-job-{conversation.public_id}-{visitor_message.id}",
            tracking_fact_summary=tracking_fact_summary,
            tracking_fact_metadata=tracking_fact_metadata,
            tracking_fact_evidence_present=bool(tracking_fact_summary),
            market_id=getattr(ticket, "market_id", None),
            language=target_language,
            runtime_context=runtime_context,
        )
    except Exception as exc:
        LOGGER.exception(
            "webchat_ai_runtime_generate_failed",
            extra={"event_payload": {
                "conversation_id": conversation.id,
                "ticket_id": ticket.id,
                "visitor_message_id": visitor_message.id,
                "error_type": type(exc).__name__,
                "error": str(exc)[:240],
            }},
        )
        _LAST_AI_REPLY_SOURCE = "private_ai_runtime"
        _LAST_AI_FALLBACK_REASON = f"ai_runtime_exception:{type(exc).__name__}"[:240]
        return ""

    _LAST_BRIDGE_ELAPSED_MS = result.elapsed_ms
    _LAST_BRIDGE_WAIT_TIMEOUT_MS = result.retry_after_ms
    _LAST_AI_REPLY_SOURCE = result.reply_source or "private_ai_runtime"
    _LAST_RUNTIME_HANDOFF_REQUIRED = bool(result.handoff_required)
    _LAST_RUNTIME_HANDOFF_REASON = result.handoff_reason
    _LAST_RUNTIME_RECOMMENDED_AGENT_ACTION = result.recommended_agent_action
    _LAST_RUNTIME_TRACE = result.runtime_trace
    _LAST_RUNTIME_RAG_TRACE = result.rag_trace
    _LAST_RUNTIME_TOOL_CALLS = result.tool_calls or []

    if not result.ok or not result.reply:
        _LAST_AI_FALLBACK_REASON = result.error_code or "ai_runtime_no_reply"
        return ""
    if not result.ai_generated:
        _LAST_AI_FALLBACK_REASON = result.error_code or "ai_runtime_non_generated_reply_blocked"
        return ""

    _LAST_AI_FALLBACK_REASON = None
    return result.reply


def _sanitize_public_ai_reply(raw: str | None) -> str:
    """Clean LLM output before storing/sending public webchat replies."""
    text = (raw or "").strip()
    if not text:
        return ""
    final_match = re.search(r"<\s*final\s*>", text, flags=re.IGNORECASE)
    if final_match:
        text = text[final_match.end():].strip()
    text = re.sub(r"<\s*think\s*>.*?<\s*/\s*think\s*>", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
    if re.search(r"<\s*think\b", text, flags=re.IGNORECASE):
        return ""
    text = re.sub(r"</?\s*(?:final|answer|response|assistant|analysis|commentary)\s*>", "", text, flags=re.IGNORECASE).strip()
    blocked_patterns = [
        r"\bSOUL\.md\b",
        r"\bsystem prompt\b",
        r"\bdeveloper message\b",
        r"\bdeveloper instruction\b",
        r"\bchain[- ]of[- ]thought\b",
        r"\bhidden reasoning\b",
        r"\binternal context\b",
        r"\binternal instruction\b",
        r"\bExternalChannel\b",
        r"\bMCP\b",
        r"\btool call\b",
        r"\baccording to .*?\.md\b",
    ]
    clean_lines = []
    for line in text.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        if re.search(r"^(analysis|reasoning|plan|thought|internal|system|developer)\s*:", candidate, flags=re.IGNORECASE):
            continue
        if any(re.search(pattern, candidate, flags=re.IGNORECASE) for pattern in blocked_patterns):
            continue
        clean_lines.append(candidate)
    text = "\n".join(clean_lines).strip()
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text
