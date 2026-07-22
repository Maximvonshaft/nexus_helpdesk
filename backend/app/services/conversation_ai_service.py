from __future__ import annotations

import asyncio
import json
from typing import Any

from sqlalchemy.orm import Session

from ..models import Customer
from ..models_agent_routing import ConversationControl
from ..utils.time import utc_now
from ..webchat_models import (
    WebchatAITurn,
    WebchatConversation,
    WebchatEvent,
    WebchatMessage,
)
from .agent_confirmation_service import (
    active_confirmation_context,
    resolve_confirmation_from_customer_message,
)
from .agent_runtime.access_policy import resolve_webchat_agent_access
from .agent_runtime.terminal_reply import customer_visible_fallback
from .ai_runtime_context import build_agent_context
from .customer_language import resolve_conversation_language
from .customer_visible_policy import evaluate_customer_visible_policy
from .webchat_ai_turn_service import (
    sanitized_ai_turn_runtime_trace,
    suppress_stale_reply_if_needed,
)
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
        if text:
            recent.append(
                {
                    "role": "customer" if row.direction == "visitor" else "assistant",
                    "text": text[:1000],
                }
            )
    return rows, recent


def _run_runtime(**kwargs: Any):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(generate_webchat_runtime_reply(**kwargs))
    raise RuntimeError("webchat_agent_runtime_event_loop_running")


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
        raise RuntimeError(
            "ticketless_ai_service_received_ticket_backed_conversation"
        )
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
    control = (
        db.query(ConversationControl)
        .filter(ConversationControl.conversation_id == conversation.id)
        .first()
    )
    language = _language_hint(visitor_message.body or "", rows)
    confirmation_resolution = resolve_confirmation_from_customer_message(
        db,
        conversation=conversation,
        message=visitor_message,
    )
    confirmation_context = (
        confirmation_resolution
        if confirmation_resolution is not None
        else active_confirmation_context(db, conversation=conversation)
    )
    access = resolve_webchat_agent_access()
    customer = (
        db.get(Customer, control.customer_id)
        if control is not None and control.customer_id is not None
        else None
    )
    runtime_context = build_agent_context(
        db,
        tenant_key=conversation.tenant_key,
        channel_key=conversation.channel_key,
        body=visitor_message.body or "",
        market_id=None,
        language=language,
        ticket=None,
        conversation=conversation,
        customer=customer,
    )
    execution_context = dict(runtime_context.get("agent_execution_context") or {})
    execution_context.update(
        {
            "conversation_id": conversation.id,
            "ticket_id": None,
            "customer_id": control.customer_id if control is not None else None,
            "country_code": control.country_code if control is not None else None,
            "ai_turn_id": turn.id if turn else None,
            "granted_permissions": sorted(access.granted_permissions),
            "actor_capabilities": sorted(access.actor_capabilities),
            "customer_confirmation_granted": bool(
                confirmation_context
                and confirmation_context.get("customer_confirmation_granted")
            ),
            "customer_confirmation_id": (
                confirmation_context.get("confirmation_id")
                if confirmation_context
                else None
            ),
            "customer_confirmation_tool_name": (
                confirmation_context.get("tool_name")
                if confirmation_context
                else None
            ),
            "customer_confirmation_arguments_sha256": (
                confirmation_context.get("arguments_sha256")
                if confirmation_context
                else None
            ),
        }
    )
    runtime_context["agent_allowed_tools"] = list(access.allowed_tools)
    runtime_context["agent_execution_context"] = execution_context
    runtime_context["customer_confirmation"] = confirmation_context
    result = _run_runtime(
        tenant_key=conversation.tenant_key,
        channel_key=conversation.channel_key,
        session_id=(
            conversation.runtime_session_id
            or (
                f"webchat:{conversation.tenant_key}:"
                f"{conversation.channel_key}:{conversation.public_id}"
            )
        ),
        body=visitor_message.body or "",
        recent_context=recent_context,
        request_id=(
            f"webchat-ai-job-{conversation.public_id}-"
            f"{visitor_message.id}"
        ),
        market_id=None,
        language=language,
        runtime_context=runtime_context,
    )
    safe_runtime_trace = sanitized_ai_turn_runtime_trace(
        result.runtime_trace
    )
    fallback_reason = result.error_code if not result.ai_generated else None
    reply_source = result.reply_source or "agent_runtime"
    handoff_required = bool(result.handoff_required)
    if not result.ok or not result.reply:
        fallback_reason = result.error_code or "agent_runtime_no_reply"
        reply_source = "agent_runtime:fallback"
        handoff_required = False
        policy = evaluate_customer_visible_policy(
            customer_visible_fallback(language, visitor_message.body or "")
        )
    else:
        policy = evaluate_customer_visible_policy(result.reply)
        if not policy.allowed or not policy.normalized_body.strip():
            fallback_reason = "customer_visible_policy_blocked"
            reply_source = "agent_runtime:fallback"
            handoff_required = False
            policy = evaluate_customer_visible_policy(
                customer_visible_fallback(language, visitor_message.body or "")
            )
    if not policy.allowed or not policy.normalized_body.strip():
        raise RuntimeError("customer_visible_fallback_rejected")

    if suppress_stale_reply_if_needed(
        db,
        conversation=conversation,
        turn=turn,
        reason="conversation_state_changed_during_ticketless_runtime",
    ):
        return {
            "status": "superseded",
            "reason": "conversation_state_changed_during_ticketless_runtime",
            "reply_source": "suppressed",
            "runtime_trace": safe_runtime_trace,
            "bridge_elapsed_ms": result.elapsed_ms,
        }

    db.expire(conversation)
    resolved_ticket_id = conversation.ticket_id
    if handoff_required and not conversation.current_handoff_request_id:
        fallback_reason = "handoff_tool_side_effect_missing"
        reply_source = "agent_runtime:fallback"
        handoff_required = False
        policy = evaluate_customer_visible_policy(
            customer_visible_fallback(language, visitor_message.body or "")
        )
        if not policy.allowed or not policy.normalized_body.strip():
            raise RuntimeError("customer_visible_fallback_rejected")

    message = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=resolved_ticket_id,
        direction="agent",
        body=policy.normalized_body,
        body_text=policy.normalized_body,
        message_type="text",
        metadata_json=json.dumps(
            {
                "generated_by": "agent_runtime",
                "reply_source": reply_source,
                "runtime_trace": safe_runtime_trace,
                "tool_calls": result.tool_calls or [],
                "runtime_handoff_required": handoff_required,
                "language": language,
                "ticketless_conversation": resolved_ticket_id is None,
                "ticket_created_in_turn": resolved_ticket_id is not None,
                "fallback": bool(fallback_reason) or not result.ai_generated,
                "fallback_reason": fallback_reason,
                "customer_confirmation": confirmation_context,
            },
            ensure_ascii=False,
            default=str,
        ),
        ai_turn_id=turn.id if turn else None,
        delivery_status="sent",
        author_label=AI_AUTHOR_LABEL,
        safety_level=policy.level,
        safety_reasons_json=json.dumps(
            policy.reasons,
            ensure_ascii=False,
        ),
        created_at=utc_now(),
    )
    db.add(message)
    db.flush()
    if turn is not None and resolved_ticket_id is not None:
        turn.ticket_id = resolved_ticket_id
        turn.updated_at = utc_now()
    _event(
        db,
        conversation=conversation,
        event_type="message.created",
        payload={
            "message_id": message.id,
            "direction": "agent",
            "ai_turn_id": turn.id if turn else None,
            "ticketless_conversation": resolved_ticket_id is None,
            "ticket_id": resolved_ticket_id,
        },
    )
    conversation.updated_at = utc_now()
    conversation.last_seen_at = utc_now()
    db.flush()
    return {
        "status": "done",
        "message_id": message.id,
        "ticket_id": resolved_ticket_id,
        "reply_source": reply_source,
        "fallback_reason": fallback_reason,
        "bridge_elapsed_ms": result.elapsed_ms,
        "runtime_trace": safe_runtime_trace,
        "runtime_handoff_required": handoff_required,
    }
