from __future__ import annotations

import asyncio
import json
from typing import Any

from sqlalchemy.orm import Session

from ..utils.time import utc_now
from ..webchat_models import WebchatAITurn, WebchatConversation, WebchatEvent, WebchatMessage
from .agent_routing_service import request_handoff
from .ai_runtime_context import build_webchat_runtime_context
from .customer_language import resolve_conversation_language
from .customer_visible_policy import evaluate_customer_visible_policy
from .tracking_fact_service import extract_tracking_number, lookup_tracking_fact
from .webchat_runtime_ai_service import generate_webchat_runtime_reply


AI_AUTHOR_LABEL = "AI Assistant"
MAX_HISTORY_MESSAGES = 12


def _event(
    db: Session,
    *,
    conversation: WebchatConversation,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> WebchatEvent:
    row = WebchatEvent(
        conversation_id=conversation.id,
        ticket_id=conversation.ticket_id,
        event_type=event_type,
        payload_json=json.dumps(payload or {}, ensure_ascii=False, default=str),
        created_at=utc_now(),
    )
    db.add(row)
    db.flush()
    return row


def _history(
    db: Session,
    *,
    conversation: WebchatConversation,
    visitor_message: WebchatMessage,
) -> tuple[list[WebchatMessage], list[dict[str, str]]]:
    rows = (
        db.query(WebchatMessage)
        .filter(WebchatMessage.conversation_id == conversation.id)
        .order_by(WebchatMessage.created_at.desc(), WebchatMessage.id.desc())
        .limit(MAX_HISTORY_MESSAGES)
        .all()
    )
    rows.reverse()
    recent: list[dict[str, str]] = []
    for row in rows:
        if row.id == visitor_message.id:
            continue
        text = (row.body_text or row.body or "").strip()
        if not text:
            continue
        role = "customer" if row.direction == "visitor" else "ai"
        recent.append({"role": role, "text": text[:500]})
    return rows, recent


def _run_runtime(**kwargs: Any):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(generate_webchat_runtime_reply(**kwargs))
    raise RuntimeError("webchat_ai_runtime_event_loop_running")


def _language_hint(text: str, rows: list[WebchatMessage]) -> str | None:
    previous = [
        row.body_text or row.body
        for row in rows
        if row.direction == "visitor"
    ]
    return resolve_conversation_language(
        text,
        previous_customer_messages=previous,
    ).language


def process_ticketless_ai_reply(
    db: Session,
    *,
    conversation: WebchatConversation,
    visitor_message: WebchatMessage,
    turn: WebchatAITurn | None,
) -> dict[str, Any]:
    if conversation.ticket_id is not None:
        raise RuntimeError("ticketless_ai_service_received_ticket_backed_conversation")
    if (conversation.channel_key or "").strip().lower() == "whatsapp":
        return {
            "status": "failed_no_public_reply",
            "reason": "ticketless_whatsapp_not_enabled",
            "reply_source": "conversation_first_guard",
        }

    existing = (
        db.query(WebchatMessage.id)
        .filter(
            WebchatMessage.conversation_id == conversation.id,
            WebchatMessage.direction == "agent",
            WebchatMessage.author_label == AI_AUTHOR_LABEL,
            WebchatMessage.id > visitor_message.id,
        )
        .first()
    )
    if existing:
        return {
            "status": "skipped",
            "reason": "agent_reply_already_exists",
            "reply_source": "existing_reply",
        }

    rows, recent_context = _history(
        db,
        conversation=conversation,
        visitor_message=visitor_message,
    )
    tracking_number = extract_tracking_number(visitor_message.body)
    if tracking_number:
        conversation.last_tracking_number = tracking_number
    elif conversation.last_tracking_number:
        tracking_number = conversation.last_tracking_number
    tracking_fact = lookup_tracking_fact(
        tracking_number=tracking_number,
        conversation_id=conversation.id,
        ticket_id=None,
        request_id=f"webchat-{conversation.public_id}-{visitor_message.id}",
    )
    tracking_summary = (
        tracking_fact.prompt_summary()
        if tracking_fact.fact_evidence_present and tracking_fact.pii_redacted
        else None
    )
    tracking_metadata = tracking_fact.metadata_payload()
    tracking_metadata.pop("fact_evidence_present", None)
    language = _language_hint(visitor_message.body or "", rows)
    runtime_context = build_webchat_runtime_context(
        db,
        tenant_key=conversation.tenant_key,
        channel_key=conversation.channel_key,
        body=visitor_message.body or "",
        language=language,
        tracking_number=tracking_number,
        tracking_fact_evidence_present=bool(tracking_summary),
        ticket=None,
        conversation=conversation,
        customer=None,
        channel_payload=tracking_metadata,
    )
    result = _run_runtime(
        tenant_key=conversation.tenant_key,
        channel_key=conversation.channel_key,
        session_id=(
            conversation.runtime_session_id
            or f"webchat:{conversation.tenant_key}:{conversation.channel_key}:{conversation.public_id}"
        ),
        body=visitor_message.body or "",
        recent_context=recent_context,
        request_id=f"webchat-ai-job-{conversation.public_id}-{visitor_message.id}",
        tracking_fact_summary=tracking_summary,
        tracking_fact_metadata=tracking_metadata,
        tracking_fact_evidence_present=bool(tracking_summary),
        market_id=None,
        language=language,
        runtime_context=runtime_context,
    )
    if not result.ok or not result.ai_generated or not result.reply:
        return {
            "status": "failed_no_public_reply",
            "reason": result.error_code or "ai_runtime_no_reply",
            "reply_source": result.reply_source,
            "runtime_trace": result.runtime_trace,
            "bridge_elapsed_ms": result.elapsed_ms,
        }
    policy = evaluate_customer_visible_policy(result.reply)
    if not policy.allowed or not policy.normalized_body.strip():
        return {
            "status": "failed_no_public_reply",
            "reason": "customer_visible_policy_blocked",
            "reply_source": result.reply_source,
            "runtime_trace": result.runtime_trace,
            "bridge_elapsed_ms": result.elapsed_ms,
        }

    message = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=None,
        direction="agent",
        body=policy.normalized_body,
        body_text=policy.normalized_body,
        message_type="text",
        metadata_json=json.dumps(
            {
                "generated_by": "webchat_ai",
                "reply_source": result.reply_source,
                "runtime_trace": result.runtime_trace,
                "tool_calls": result.tool_calls or [],
                "ticketless_conversation": True,
            },
            ensure_ascii=False,
            default=str,
        ),
        ai_turn_id=turn.id if turn else None,
        delivery_status="sent",
        author_label=AI_AUTHOR_LABEL,
        safety_level=policy.level,
        safety_reasons_json=json.dumps(policy.reasons, ensure_ascii=False),
        created_at=utc_now(),
    )
    db.add(message)
    db.flush()
    _event(
        db,
        conversation=conversation,
        event_type="message.created",
        payload={
            "message_id": message.id,
            "direction": "agent",
            "ai_turn_id": turn.id if turn else None,
            "ticketless_conversation": True,
        },
    )
    conversation.updated_at = utc_now()
    conversation.last_seen_at = utc_now()

    if result.handoff_required:
        request_handoff(
            db,
            conversation=conversation,
            source="ai_runtime",
            trigger_type="runtime_handoff",
            reason_code=(
                result.handoff_reason or "ai_runtime_requested_handoff"
            )[:160],
            reason_text=result.handoff_reason,
            recommended_agent_action=result.recommended_agent_action,
            trigger_message_id=visitor_message.id,
            ai_turn_id=turn.id if turn else None,
            requested_by_actor_type="ai_runtime",
        )

    db.flush()
    return {
        "status": "done",
        "message_id": message.id,
        "reply_source": result.reply_source,
        "fallback_reason": None,
        "fact_evidence_present": bool(tracking_summary),
        "bridge_elapsed_ms": result.elapsed_ms,
        "runtime_trace": result.runtime_trace,
    }
