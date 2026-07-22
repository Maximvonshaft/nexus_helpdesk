from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..enums import (
    ConversationState,
    EventType,
    MessageStatus,
    SourceChannel,
    TicketStatus,
)
from ..models import Customer, Ticket, TicketEvent
from ..models_agent_routing import ConversationControl
from ..settings import get_settings
from ..utils.time import ensure_utc, utc_now
from ..webchat_models import WebchatAITurn, WebchatConversation, WebchatMessage
from .agent_runtime.access_policy import resolve_webchat_agent_access
from .agent_runtime.terminal_reply import customer_visible_fallback
from .ai_reply_contract import AI_REPLY_CONTRACT, build_ai_reply_contract
from .ai_runtime_context import build_agent_context
from .customer_language import resolve_conversation_language
from .customer_visible_message_service import create_customer_visible_message
from .customer_visible_policy import evaluate_customer_visible_policy
from .sla_service import evaluate_sla, update_first_response
from .webchat_ai_turn_service import (
    is_ai_suspended_for_handoff,
    safe_write_webchat_event,
    sanitized_ai_turn_runtime_trace,
    suppress_stale_reply_if_needed,
    supersede_ai_turn,
)
from .webchat_runtime_ai_service import (
    WebchatRuntimeReplyResult,
    generate_webchat_runtime_reply,
)

LOGGER = logging.getLogger("nexusdesk")
settings = get_settings()
AI_AUTHOR_LABEL = "AI Assistant"
MAX_HISTORY_MESSAGES = 12


def process_webchat_ai_reply_job(
    db: Session,
    *,
    conversation_id: int,
    ticket_id: int | None,
    visitor_message_id: int,
    ai_turn_id: int | None = None,
) -> dict[str, Any]:
    conversation = db.get(WebchatConversation, conversation_id)
    visitor_message = db.get(WebchatMessage, visitor_message_id)
    ticket = db.get(Ticket, ticket_id) if ticket_id is not None else None
    if conversation is None or visitor_message is None:
        raise RuntimeError("webchat runtime context not found")
    if visitor_message.conversation_id != conversation.id:
        raise RuntimeError("webchat runtime context mismatch")
    if conversation.ticket_id is None:
        if ticket is not None or visitor_message.ticket_id is not None:
            raise RuntimeError("ticketless webchat job payload mismatch")
    elif (
        ticket is None
        or ticket.id != conversation.ticket_id
        or visitor_message.ticket_id != ticket.id
    ):
        raise RuntimeError("ticket-backed webchat job payload mismatch")
    if ticket is None and _is_whatsapp_conversation(conversation):
        return {
            "status": "failed_no_public_reply",
            "reason": "ticketless_whatsapp_not_enabled",
            "reply_source": "conversation_first_guard",
        }

    turn = db.get(WebchatAITurn, ai_turn_id) if ai_turn_id else None
    if is_ai_suspended_for_handoff(conversation):
        return {
            "status": "skipped",
            "reason": "handoff_ai_suspended",
            "reply_source": "suppressed",
        }
    if _agent_reply_exists(
        db,
        conversation=conversation,
        visitor_message=visitor_message,
    ):
        return {
            "status": "skipped",
            "reason": "agent_reply_already_exists",
            "reply_source": "existing_reply",
        }
    if suppress_stale_reply_if_needed(
        db,
        conversation=conversation,
        turn=turn,
        reason="newer_message_before_runtime_reply",
    ):
        return {
            "status": "superseded",
            "reason": "newer_message_before_runtime_reply",
            "reply_source": "suppressed",
        }

    history_rows = _history(db, conversation=conversation)
    language = _language_hint(
        visitor_message.body,
        history_rows=history_rows,
    )
    access = resolve_webchat_agent_access()
    customer = _customer_for_conversation(
        db,
        conversation=conversation,
        ticket=ticket,
    )
    runtime_context = build_agent_context(
        db,
        tenant_key=conversation.tenant_key,
        channel_key=conversation.channel_key,
        body=visitor_message.body or "",
        market_id=getattr(ticket, "market_id", None),
        language=language,
        ticket=ticket,
        conversation=conversation,
        customer=customer,
    )
    execution_context = dict(
        runtime_context.get("agent_execution_context") or {}
    )
    control = _conversation_control(db, conversation=conversation)
    execution_context.update(
        {
            "conversation_id": conversation.id,
            "ticket_id": ticket.id if ticket else None,
            "customer_id": (
                ticket.customer_id
                if ticket
                else control.customer_id
                if control
                else None
            ),
            "country_code": (
                ticket.country_code
                if ticket
                else control.country_code
                if control
                else None
            ),
            "ai_turn_id": ai_turn_id,
            "granted_permissions": sorted(access.granted_permissions),
            "actor_capabilities": sorted(access.actor_capabilities),
        }
    )
    runtime_context["agent_allowed_tools"] = list(access.allowed_tools)
    runtime_context["agent_execution_context"] = execution_context
    session_policy = _session_policy(
        conversation,
        total_messages=_message_count(db, conversation=conversation),
        history_count=len(history_rows),
    )
    result = _run_runtime_reply_sync(
        tenant_key=conversation.tenant_key,
        channel_key=conversation.channel_key,
        session_id=session_policy["session_key"],
        body=visitor_message.body or "",
        recent_context=_history_as_runtime_context(
            history_rows=history_rows,
            visitor_message=visitor_message,
        ),
        request_id=(
            f"webchat-ai-job-{conversation.public_id}-{visitor_message.id}"
        ),
        market_id=getattr(ticket, "market_id", None),
        language=language,
        runtime_context=runtime_context,
    )
    safe_trace = sanitized_ai_turn_runtime_trace(result.runtime_trace)
    public = _public_reply_decision(
        result=result,
        language=language,
        customer_message=visitor_message.body or "",
    )

    committed_handoff = _committed_handoff_owns_conversation(
        db,
        conversation_id=conversation.id,
    )
    if turn is not None and committed_handoff:
        supersede_ai_turn(
            db,
            conversation=conversation,
            turn=turn,
            reason="handoff_started_before_reply_commit",
        )
        return {
            "status": "superseded",
            "reason": "handoff_started_before_reply_commit",
            "reply_source": "suppressed",
            "runtime_trace": safe_trace,
            "bridge_elapsed_ms": result.elapsed_ms,
        }
    if suppress_stale_reply_if_needed(
        db,
        conversation=conversation,
        turn=turn,
        reason="newer_message_before_reply_commit",
    ):
        return {
            "status": "superseded",
            "reason": "newer_message_before_reply_commit",
            "reply_source": "suppressed",
            "runtime_trace": safe_trace,
            "bridge_elapsed_ms": result.elapsed_ms,
        }

    db.expire(conversation)
    if ticket is not None:
        db.expire(ticket)
    if public["handoff_required"] and not conversation.current_handoff_request_id:
        public = _fallback_decision(
            language=language,
            customer_message=visitor_message.body or "",
            reason="handoff_tool_side_effect_missing",
        )

    now = utc_now()
    final_body = public["body"]
    if ticket is not None:
        message, outbound_message = _persist_ticket_reply(
            db,
            conversation=conversation,
            ticket=ticket,
            visitor_message=visitor_message,
            ai_turn_id=ai_turn_id,
            result=result,
            safe_trace=safe_trace,
            session_policy=session_policy,
            public=public,
        )
        if is_ai_suspended_for_handoff(conversation):
            ticket.required_action = (
                result.recommended_agent_action
                or "Human review requested by Agent"
            )
            ticket.conversation_state = ConversationState.human_review_required
        else:
            ticket.status = TicketStatus.waiting_customer
            ticket.conversation_state = ConversationState.waiting_customer
        update_first_response(ticket)
        ticket.last_ai_update = final_body
        ticket.last_runtime_reply_at = now
        ticket.updated_at = now
        evaluate_sla(ticket, db)
        outbound_message_id = outbound_message.id
    else:
        message = _persist_ticketless_reply(
            db,
            conversation=conversation,
            ai_turn_id=ai_turn_id,
            result=result,
            safe_trace=safe_trace,
            session_policy=session_policy,
            language=language,
            public=public,
        )
        outbound_message_id = None

    conversation.updated_at = now
    conversation.last_seen_at = now
    db.flush()
    event_payload = {
        "public_conversation_id": conversation.public_id,
        "conversation_id": conversation.id,
        "ticket_id": ticket.id if ticket else None,
        "visitor_message_id": visitor_message.id,
        "ai_turn_id": ai_turn_id,
        "webchat_message_id": message.id,
        "outbound_message_id": outbound_message_id,
        "reply_source": public["reply_source"],
        "runtime_trace": safe_trace,
        "tool_calls": result.tool_calls or [],
        "ai_generated": result.ai_generated,
        "runtime_error_code": result.error_code,
    }
    safe_write_webchat_event(
        db,
        conversation_id=conversation.id,
        ticket_id=ticket.id if ticket else None,
        event_type="agent_runtime.reply_committed",
        payload=event_payload,
    )
    if ticket is not None:
        db.add(
            TicketEvent(
                ticket_id=ticket.id,
                actor_id=None,
                event_type=EventType.internal_note_added,
                note="Agent runtime completed",
                payload_json=json.dumps(
                    event_payload,
                    ensure_ascii=False,
                    default=str,
                ),
            )
        )
    LOGGER.info(
        "webchat_agent_reply_sent",
        extra={"event_payload": event_payload},
    )
    return {
        "status": "done",
        "message_id": message.id,
        "reply_source": public["reply_source"],
        "fallback": public["fallback"],
        "fallback_reason": public["fallback_reason"],
        "bridge_elapsed_ms": result.elapsed_ms,
        "runtime_trace": safe_trace,
        "runtime_handoff_required": public["handoff_required"],
    }


def _committed_handoff_owns_conversation(
    db: Session,
    *,
    conversation_id: int,
) -> bool:
    """Read the committed ownership state outside the worker Session snapshot."""

    bind = db.get_bind()
    engine = getattr(bind, "engine", bind)
    if engine is None:
        return False
    with engine.connect() as connection:
        row = connection.execute(
            select(
                WebchatConversation.ai_suspended,
                WebchatConversation.handoff_status,
                WebchatConversation.active_agent_id,
            ).where(WebchatConversation.id == conversation_id)
        ).first()
    if row is None:
        return True
    return bool(
        row[0]
        or str(row[1] or "") in {"requested", "accepted"}
        or row[2] is not None
    )


def _public_reply_decision(
    *,
    result: WebchatRuntimeReplyResult,
    language: str | None,
    customer_message: str,
) -> dict[str, Any]:
    if not result.ok or not result.reply:
        return _fallback_decision(
            language=language,
            customer_message=customer_message,
            reason=result.error_code or "agent_runtime_no_reply",
        )
    body = _sanitize_public_ai_reply(result.reply)
    if not body:
        return _fallback_decision(
            language=language,
            customer_message=customer_message,
            reason="agent_runtime_no_reply",
        )
    policy = evaluate_customer_visible_policy(body)
    if not policy.allowed or not policy.normalized_body.strip():
        return _fallback_decision(
            language=language,
            customer_message=customer_message,
            reason="customer_visible_policy_blocked",
        )
    return {
        "body": policy.normalized_body,
        "policy": policy,
        "reply_source": result.reply_source or "agent_runtime",
        "fallback": not result.ai_generated,
        "fallback_reason": result.error_code if not result.ai_generated else None,
        "handoff_required": bool(result.handoff_required),
    }


def _fallback_decision(
    *,
    language: str | None,
    customer_message: str,
    reason: str,
) -> dict[str, Any]:
    policy = evaluate_customer_visible_policy(
        customer_visible_fallback(language, customer_message)
    )
    if not policy.allowed or not policy.normalized_body.strip():
        raise RuntimeError("customer_visible_fallback_rejected")
    return {
        "body": policy.normalized_body,
        "policy": policy,
        "reply_source": "agent_runtime:fallback",
        "fallback": True,
        "fallback_reason": reason,
        "handoff_required": False,
    }


def _persist_ticket_reply(
    db: Session,
    *,
    conversation: WebchatConversation,
    ticket: Ticket,
    visitor_message: WebchatMessage,
    ai_turn_id: int | None,
    result: WebchatRuntimeReplyResult,
    safe_trace: dict[str, Any] | None,
    session_policy: dict[str, Any],
    public: dict[str, Any],
) -> tuple[WebchatMessage, Any]:
    external = _is_whatsapp_conversation(conversation)
    channel = SourceChannel.whatsapp if external else SourceChannel.web_chat
    provider_status = (
        "whatsapp_ai_reply_queued" if external else "webchat_ai_delivered"
    )
    contract = _reply_contract(
        result=result,
        safe_trace=safe_trace,
        channel=channel,
        public=public,
    )
    visible = create_customer_visible_message(
        db,
        ticket=ticket,
        conversation=conversation,
        channel=channel,
        body=public["body"],
        origin="provider_runtime",
        created_by=None,
        provider_status=provider_status,
        ai_contract=contract,
        outbound_status=None if external else MessageStatus.sent,
        ai_turn_id=ai_turn_id,
        delivery_status="queued" if external else "sent",
        metadata_json=_message_metadata(
            result=result,
            safe_trace=safe_trace,
            session_policy=session_policy,
            external_send=external,
            ai_turn_id=ai_turn_id,
            ticketless=False,
            public=public,
        ),
        author_label=AI_AUTHOR_LABEL,
        safety_level=public["policy"].level,
        safety_reasons_json=json.dumps(
            public["policy"].reasons,
            ensure_ascii=False,
        ),
        event_type=(
            EventType.outbound_queued if external else EventType.outbound_sent
        ),
        event_note=(
            "WhatsApp Agent reply queued"
            if external
            else "Webchat Agent reply sent"
        ),
        event_payload={
            "public_conversation_id": conversation.public_id,
            "conversation_id": conversation.id,
            "ticket_id": ticket.id,
            "visitor_message_id": visitor_message.id,
            "ai_turn_id": ai_turn_id,
            "reply_source": public["reply_source"],
            "provider_status": provider_status,
            "external_send": external,
            "runtime_trace": safe_trace,
            "tool_calls": result.tool_calls or [],
            "ai_generated": result.ai_generated,
            "runtime_error_code": result.error_code,
        },
    )
    if visible.webchat_message is None or visible.outbound_message is None:
        raise RuntimeError("customer_visible_message_not_created")
    return visible.webchat_message, visible.outbound_message


def _persist_ticketless_reply(
    db: Session,
    *,
    conversation: WebchatConversation,
    ai_turn_id: int | None,
    result: WebchatRuntimeReplyResult,
    safe_trace: dict[str, Any] | None,
    session_policy: dict[str, Any],
    language: str | None,
    public: dict[str, Any],
) -> WebchatMessage:
    channel = SourceChannel.web_chat
    metadata = _message_metadata(
        result=result,
        safe_trace=safe_trace,
        session_policy=session_policy,
        external_send=False,
        ai_turn_id=ai_turn_id,
        ticketless=True,
        public=public,
    )
    metadata["language"] = language
    visible = create_customer_visible_message(
        db,
        ticket=None,
        conversation=conversation,
        channel=channel,
        body=public["body"],
        origin="provider_runtime",
        created_by=None,
        provider_status="webchat_ai_delivered",
        ai_contract=_reply_contract(
            result=result,
            safe_trace=safe_trace,
            channel=channel,
            public=public,
        ),
        outbound_status=MessageStatus.sent,
        ai_turn_id=ai_turn_id,
        delivery_status="sent",
        metadata_json=metadata,
        author_label=AI_AUTHOR_LABEL,
        safety_level=public["policy"].level,
        safety_reasons_json=json.dumps(
            public["policy"].reasons,
            ensure_ascii=False,
        ),
        create_external_comment=False,
    )
    if visible.webchat_message is None or visible.outbound_message is not None:
        raise RuntimeError("ticketless_customer_visible_message_not_created")
    return visible.webchat_message


def _reply_contract(
    *,
    result: WebchatRuntimeReplyResult,
    safe_trace: dict[str, Any] | None,
    channel: SourceChannel,
    public: dict[str, Any],
):
    return build_ai_reply_contract(
        body=public["body"],
        runtime_trace=safe_trace or {},
        safety_status="passed",
        **_ai_reply_contract_fields(
            body=public["body"],
            channel=channel,
            handoff_required=public["handoff_required"],
            runtime_trace=safe_trace,
            reply_type=_reply_type(result, public["body"]),
        ),
    )


def _conversation_control(
    db: Session,
    *,
    conversation: WebchatConversation,
) -> ConversationControl | None:
    return (
        db.query(ConversationControl)
        .filter(ConversationControl.conversation_id == conversation.id)
        .first()
    )


def _customer_for_conversation(
    db: Session,
    *,
    conversation: WebchatConversation,
    ticket: Ticket | None,
) -> Customer | None:
    if ticket is not None and ticket.customer_id is not None:
        return db.get(Customer, ticket.customer_id)
    control = _conversation_control(db, conversation=conversation)
    return (
        db.get(Customer, control.customer_id)
        if control is not None and control.customer_id is not None
        else None
    )


def _history(
    db: Session,
    *,
    conversation: WebchatConversation,
) -> list[WebchatMessage]:
    rows = (
        db.query(WebchatMessage)
        .filter(WebchatMessage.conversation_id == conversation.id)
        .order_by(WebchatMessage.created_at.desc(), WebchatMessage.id.desc())
        .limit(MAX_HISTORY_MESSAGES)
        .all()
    )
    rows.reverse()
    return rows


def _message_count(db: Session, *, conversation: WebchatConversation) -> int:
    return (
        db.query(WebchatMessage.id)
        .filter(WebchatMessage.conversation_id == conversation.id)
        .count()
    )


def _session_policy(
    conversation: WebchatConversation,
    *,
    total_messages: int,
    history_count: int,
) -> dict[str, Any]:
    ttl_hours = max(
        1,
        int(getattr(settings, "webchat_ai_session_ttl_hours", 24) or 24),
    )
    max_messages = max(
        4,
        int(getattr(settings, "webchat_ai_session_max_messages", 40) or 40),
    )
    generation_by_messages = total_messages // max_messages
    created_at = ensure_utc(conversation.created_at) or utc_now()
    generation_by_ttl = int(
        (utc_now() - created_at) // timedelta(hours=ttl_hours)
    )
    generation = max(generation_by_messages, generation_by_ttl)
    base = conversation.runtime_session_id or (
        f"webchat:{conversation.tenant_key}:"
        f"{conversation.channel_key}:{conversation.public_id}"
    )
    return {
        "session_key": base if generation <= 0 else f"{base}:g{generation}",
        "generation": generation,
        "rotation_reason": "ttl_or_message_limit" if generation > 0 else None,
        "history_count": history_count,
    }


def _history_as_runtime_context(
    *,
    history_rows: list[WebchatMessage],
    visitor_message: WebchatMessage,
) -> list[dict[str, str]]:
    return [
        {
            "role": "customer" if row.direction == "visitor" else "assistant",
            "text": (row.body_text or row.body or "").strip()[:1000],
        }
        for row in history_rows[-MAX_HISTORY_MESSAGES:]
        if row.id != visitor_message.id
        and (row.body_text or row.body or "").strip()
    ]


def _language_hint(
    text: str | None,
    *,
    history_rows: list[WebchatMessage],
) -> str | None:
    previous = [
        row.body_text or row.body
        for row in history_rows
        if row.direction == "visitor"
    ]
    return resolve_conversation_language(
        text,
        previous_customer_messages=previous,
    ).language


def _run_runtime_reply_sync(**kwargs: Any) -> WebchatRuntimeReplyResult:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(generate_webchat_runtime_reply(**kwargs))
    raise RuntimeError("webchat_agent_runtime_event_loop_running")


def _sanitize_public_ai_reply(raw: str | None) -> str:
    text = " ".join(str(raw or "").strip().split())
    if not text or re.search(r"<\s*think\b", text, flags=re.IGNORECASE):
        return ""
    return re.sub(
        r"</?\s*(?:final|answer|response|assistant)\s*>",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()


def _is_whatsapp_conversation(conversation: WebchatConversation) -> bool:
    return (
        str(conversation.channel_key or "").lower()
        == SourceChannel.whatsapp.value
    )


def _agent_reply_exists(
    db: Session,
    *,
    conversation: WebchatConversation,
    visitor_message: WebchatMessage,
) -> bool:
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


def _ai_reply_contract_fields(
    *,
    body: str,
    channel: SourceChannel,
    handoff_required: bool,
    runtime_trace: dict[str, Any] | None,
    reply_type: str | None = None,
) -> dict[str, Any]:
    del body
    trace = runtime_trace if isinstance(runtime_trace, dict) else {}
    decision = (
        trace.get("ai_decision")
        if isinstance(trace.get("ai_decision"), dict)
        else {}
    )
    sources = ["context:customer_message"]
    for tool in trace.get("executed_tools") or []:
        if isinstance(tool, dict) and tool.get("tool_name"):
            sources.append(f"tool:{tool['tool_name']}"[:240])
    confidence = decision.get("confidence")
    try:
        confidence = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence = 0.5
    return {
        "contract_version": AI_REPLY_CONTRACT,
        "reply_type": reply_type or (
            "handoff_notice" if handoff_required else "answer"
        ),
        "used_sources": list(dict.fromkeys(sources)),
        "unsupported_claims": [],
        "conflicts": [],
        "confidence": confidence,
        "channel": channel.value,
    }


def _reply_type(result: WebchatRuntimeReplyResult, body: str) -> str:
    if result.handoff_required:
        return "handoff_notice"
    if body.rstrip().endswith(("?", "？")):
        return "clarifying_question"
    return "answer"


def _message_metadata(
    *,
    result: WebchatRuntimeReplyResult,
    safe_trace: dict[str, Any] | None,
    session_policy: dict[str, Any],
    external_send: bool,
    ai_turn_id: int | None,
    ticketless: bool,
    public: dict[str, Any],
) -> dict[str, Any]:
    return {
        "generated_by": "agent_runtime",
        "intent": result.intent,
        "fallback_reason": public["fallback_reason"],
        "fallback": public["fallback"],
        "external_send": external_send,
        "reply_source": public["reply_source"],
        "provider_session_key": session_policy["session_key"],
        "provider_session_generation": session_policy["generation"],
        "runtime_trace": safe_trace,
        "tool_calls": result.tool_calls or [],
        "ai_turn_id": ai_turn_id,
        "ticketless_conversation": ticketless,
        "runtime_handoff_required": public["handoff_required"],
    }
