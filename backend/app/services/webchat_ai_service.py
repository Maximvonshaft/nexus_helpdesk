from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from ..enums import ConversationState, EventType, MessageStatus, SourceChannel, TicketStatus
from ..models import Ticket, TicketEvent
from ..settings import get_settings
from ..utils.time import ensure_utc, utc_now
from ..webchat_models import WebchatAITurn, WebchatConversation, WebchatMessage
from .agent_runtime.access_policy import resolve_webchat_agent_access
from .ai_runtime_context import build_agent_context
from .ai_reply_contract import AI_REPLY_CONTRACT, build_ai_reply_contract
from .customer_language import resolve_conversation_language
from .customer_visible_message_service import create_customer_visible_message
from .customer_visible_policy import evaluate_customer_visible_policy
from .sla_service import evaluate_sla, update_first_response
from .webchat_ai_turn_service import is_ai_suspended_for_handoff, safe_write_webchat_event, suppress_stale_reply_if_needed
from .webchat_runtime_ai_service import WebchatRuntimeReplyResult, generate_webchat_runtime_reply

LOGGER = logging.getLogger("nexusdesk")
settings = get_settings()
AI_AUTHOR_LABEL = "AI Assistant"
MAX_HISTORY_MESSAGES = 12


def process_webchat_ai_reply_job(
    db: Session,
    *,
    conversation_id: int,
    ticket_id: int,
    visitor_message_id: int,
    ai_turn_id: int | None = None,
) -> dict[str, Any]:
    conversation = db.query(WebchatConversation).filter(WebchatConversation.id == conversation_id).first()
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    visitor_message = db.query(WebchatMessage).filter(WebchatMessage.id == visitor_message_id).first()
    if conversation is None or ticket is None or visitor_message is None:
        raise RuntimeError("webchat runtime context not found")
    if visitor_message.conversation_id != conversation.id or visitor_message.ticket_id != ticket.id:
        raise RuntimeError("webchat runtime context mismatch")
    turn = db.get(WebchatAITurn, ai_turn_id) if ai_turn_id else None

    if is_ai_suspended_for_handoff(conversation):
        return {"status": "skipped", "reason": "handoff_ai_suspended", "reply_source": "suppressed"}
    if _agent_reply_exists(db, conversation=conversation, visitor_message=visitor_message):
        return {"status": "skipped", "reason": "agent_reply_already_exists", "reply_source": "existing_reply"}
    if suppress_stale_reply_if_needed(db, conversation=conversation, turn=turn, reason="newer_message_before_runtime_reply"):
        return {"status": "superseded", "reason": "newer_message_before_runtime_reply", "reply_source": "suppressed"}

    history_rows = (
        db.query(WebchatMessage)
        .filter(WebchatMessage.conversation_id == conversation.id)
        .order_by(WebchatMessage.created_at.desc(), WebchatMessage.id.desc())
        .limit(MAX_HISTORY_MESSAGES)
        .all()
    )
    history_rows.reverse()
    total_messages = db.query(WebchatMessage.id).filter(WebchatMessage.conversation_id == conversation.id).count()
    session_policy = _webchat_session_policy(conversation, history_rows, total_messages)
    language = _language_hint(visitor_message.body, history_rows=history_rows)
    access = resolve_webchat_agent_access()
    runtime_context = build_agent_context(
        db,
        tenant_key=conversation.tenant_key,
        channel_key=conversation.channel_key,
        body=visitor_message.body or "",
        market_id=getattr(ticket, "market_id", None),
        language=language,
        ticket=ticket,
        conversation=conversation,
        customer=getattr(ticket, "customer", None),
    )
    execution_context = dict(runtime_context.get("agent_execution_context") or {})
    execution_context.update(
        {
            "conversation_id": conversation.id,
            "ticket_id": ticket.id,
            "customer_id": getattr(ticket, "customer_id", None),
            "country_code": getattr(ticket, "country_code", None),
            "ai_turn_id": ai_turn_id,
            "granted_permissions": sorted(access.granted_permissions),
            "actor_capabilities": sorted(access.actor_capabilities),
        }
    )
    runtime_context["agent_allowed_tools"] = list(access.allowed_tools)
    runtime_context["agent_execution_context"] = execution_context
    result = _run_runtime_reply_sync(
        tenant_key=conversation.tenant_key,
        channel_key=conversation.channel_key,
        session_id=session_policy["session_key"],
        body=visitor_message.body or "",
        recent_context=_history_as_runtime_context(history_rows=history_rows, visitor_message=visitor_message),
        request_id=f"webchat-ai-job-{conversation.public_id}-{visitor_message.id}",
        market_id=getattr(ticket, "market_id", None),
        language=language,
        runtime_context=runtime_context,
    )

    final_body = _sanitize_public_ai_reply(result.reply)
    if not final_body:
        final_body = _localized_fallback(language, visitor_message.body or "")
    policy = evaluate_customer_visible_policy(final_body)
    if not policy.allowed:
        final_body = _localized_fallback(language, visitor_message.body or "")
        policy = evaluate_customer_visible_policy(final_body)
        if not policy.allowed:  # pragma: no cover - static fallback should always pass
            raise RuntimeError("customer_visible_fallback_rejected")
    final_body = policy.normalized_body

    if suppress_stale_reply_if_needed(db, conversation=conversation, turn=turn, reason="newer_message_before_reply_commit"):
        return {"status": "superseded", "reason": "newer_message_before_reply_commit", "reply_source": "suppressed"}

    is_external_whatsapp = _is_whatsapp_conversation(conversation)
    reply_channel = SourceChannel.whatsapp if is_external_whatsapp else SourceChannel.web_chat
    delivery_status = "queued" if is_external_whatsapp else "sent"
    provider_status = "whatsapp_ai_reply_queued" if is_external_whatsapp else "webchat_ai_delivered"
    reply_type = _reply_type(result, final_body)
    contract_fields = _ai_reply_contract_fields(
        body=final_body,
        channel=reply_channel,
        handoff_required=result.handoff_required,
        runtime_trace=result.runtime_trace,
        reply_type=reply_type,
    )
    ai_contract = build_ai_reply_contract(
        body=final_body,
        runtime_trace=result.runtime_trace,
        safety_status="passed",
        **contract_fields,
    )

    # A handoff is a Tool side effect, not a reply-side business shortcut.
    # Refresh the rows because the Agent Tool Executor uses its own transaction.
    db.expire(conversation)
    db.expire(ticket)
    if is_ai_suspended_for_handoff(conversation):
        ticket.required_action = result.recommended_agent_action or "Human review requested by Agent"
        ticket.conversation_state = ConversationState.human_review_required
    else:
        ticket.status = TicketStatus.waiting_customer
        ticket.conversation_state = ConversationState.waiting_customer

    update_first_response(ticket)
    ticket.last_ai_update = final_body
    ticket.last_runtime_reply_at = utc_now()
    ticket.updated_at = utc_now()
    conversation.updated_at = utc_now()
    conversation.last_seen_at = utc_now()

    outbound_event_type = EventType.outbound_queued if is_external_whatsapp else EventType.outbound_sent
    event_payload = {
        "public_conversation_id": conversation.public_id,
        "conversation_id": conversation.id,
        "ticket_id": ticket.id,
        "visitor_message_id": visitor_message.id,
        "ai_turn_id": ai_turn_id,
        "reply_source": result.reply_source,
        "provider_status": provider_status,
        "external_send": is_external_whatsapp,
        "runtime_trace": result.runtime_trace,
        "tool_calls": result.tool_calls or [],
        "ai_generated": result.ai_generated,
        "runtime_error_code": result.error_code,
    }
    visible = create_customer_visible_message(
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
            result=result,
            session_policy=session_policy,
            external_send=is_external_whatsapp,
            ai_turn_id=ai_turn_id,
        ),
        author_label=AI_AUTHOR_LABEL,
        safety_level=policy.level,
        safety_reasons_json=json.dumps(policy.reasons, ensure_ascii=False),
        event_type=outbound_event_type,
        event_note="WhatsApp Agent reply queued" if is_external_whatsapp else "Webchat Agent reply sent",
        event_payload=event_payload,
    )
    message = visible.webchat_message
    outbound_message = visible.outbound_message
    if message is None or outbound_message is None:
        raise RuntimeError("customer_visible_message_not_created")

    event_payload["webchat_message_id"] = message.id
    event_payload["outbound_message_id"] = outbound_message.id
    safe_write_webchat_event(
        db,
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        event_type="agent_runtime.reply_committed",
        payload=event_payload,
    )
    db.add(
        TicketEvent(
            ticket_id=ticket.id,
            actor_id=None,
            event_type=EventType.internal_note_added,
            note="Agent runtime completed",
            payload_json=json.dumps(event_payload, ensure_ascii=False, default=str),
        )
    )
    evaluate_sla(ticket, db)
    LOGGER.info("webchat_agent_reply_sent", extra={"event_payload": event_payload})
    return {
        "status": "done",
        "message_id": message.id,
        "reply_source": result.reply_source,
        "fallback": not result.ai_generated,
        "fallback_reason": result.error_code if not result.ai_generated else None,
        "bridge_elapsed_ms": result.elapsed_ms,
        "runtime_trace": result.runtime_trace,
    }


def _ai_reply_contract_fields(
    *,
    body: str,
    channel: SourceChannel,
    handoff_required: bool,
    runtime_trace: dict[str, Any] | None,
    reply_type: str | None = None,
    **_legacy: Any,
) -> dict[str, Any]:
    trace = runtime_trace if isinstance(runtime_trace, dict) else {}
    decision = trace.get("ai_decision") if isinstance(trace.get("ai_decision"), dict) else {}
    sources = ["context:customer_message"]
    for tool in trace.get("executed_tools") or []:
        if isinstance(tool, dict) and tool.get("tool_name"):
            sources.append(f"tool:{tool['tool_name']}"[:240])
    sources = list(dict.fromkeys(sources))
    confidence = decision.get("confidence")
    try:
        confidence = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence = 0.5
    return {
        "contract_version": AI_REPLY_CONTRACT,
        "reply_type": reply_type or ("handoff_notice" if handoff_required else "answer"),
        "used_sources": sources,
        "unsupported_claims": [],
        "conflicts": [],
        "confidence": confidence,
        "channel": channel.value,
    }


def _reply_type(result: WebchatRuntimeReplyResult, body: str) -> str:
    if result.handoff_required:
        return "handoff_notice"
    if result.intent in {"unclear", "tracking_missing_number", "request_information"} or body.rstrip().endswith(("?", "？")):
        return "clarifying_question"
    return "answer"


def _message_metadata(
    *,
    result: WebchatRuntimeReplyResult,
    session_policy: dict[str, Any],
    external_send: bool,
    ai_turn_id: int | None,
) -> str:
    return json.dumps(
        {
            "generated_by": "agent_runtime",
            "intent": result.intent,
            "fallback_reason": result.error_code if not result.ai_generated else None,
            "external_send": external_send,
            "reply_source": result.reply_source,
            "provider_session_key": session_policy["session_key"],
            "provider_session_generation": session_policy["generation"],
            "runtime_trace": result.runtime_trace,
            "tool_calls": result.tool_calls or [],
            "ai_turn_id": ai_turn_id,
        },
        ensure_ascii=False,
        default=str,
    )


def _webchat_session_policy(
    conversation: WebchatConversation,
    history_rows: list[WebchatMessage],
    total_messages: int,
) -> dict[str, Any]:
    ttl_hours = max(1, int(getattr(settings, "webchat_ai_session_ttl_hours", 24) or 24))
    max_messages = max(4, int(getattr(settings, "webchat_ai_session_max_messages", 40) or 40))
    generation_by_messages = total_messages // max_messages
    created_at = ensure_utc(conversation.created_at) or utc_now()
    generation_by_ttl = int((utc_now() - created_at) // timedelta(hours=ttl_hours))
    generation = max(generation_by_messages, generation_by_ttl)
    base_key = f"webchat:{conversation.tenant_key}:{conversation.channel_key}:{conversation.public_id}"
    return {
        "session_key": base_key if generation <= 0 else f"{base_key}:g{generation}",
        "generation": generation,
        "rotation_reason": "ttl_or_message_limit" if generation > 0 else None,
        "history_count": len(history_rows),
    }


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
        if text:
            recent.append({"role": "customer" if row.direction == "visitor" else "assistant", "text": text[:1000]})
    return recent


def _language_hint(text: str | None, *, history_rows: list[WebchatMessage]) -> str | None:
    previous = [row.body_text or row.body for row in history_rows if row.direction == "visitor"]
    return resolve_conversation_language(text, previous_customer_messages=previous).language


def _run_runtime_reply_sync(**kwargs: Any) -> WebchatRuntimeReplyResult:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(generate_webchat_runtime_reply(**kwargs))
    raise RuntimeError("webchat_agent_runtime_event_loop_running")


def _sanitize_public_ai_reply(raw: str | None) -> str:
    text = " ".join(str(raw or "").strip().split())
    if not text:
        return ""
    if re.search(r"<\s*think\b", text, flags=re.IGNORECASE):
        return ""
    return re.sub(r"</?\s*(?:final|answer|response|assistant)\s*>", "", text, flags=re.IGNORECASE).strip()


def _localized_fallback(language: str | None, body: str) -> str:
    if language == "zh" or any("\u4e00" <= char <= "\u9fff" for char in body):
        return "抱歉，我暂时无法完成这次处理。请稍后重试，或者告诉我是否需要转人工客服。"
    if language == "de":
        return "Entschuldigung, ich konnte diese Anfrage gerade nicht abschließen. Bitte versuchen Sie es erneut oder bitten Sie um menschlichen Support."
    return "Sorry, I could not complete that request right now. Please try again or ask for human support."


def _is_whatsapp_conversation(conversation: WebchatConversation) -> bool:
    return str(conversation.channel_key or "").lower() == SourceChannel.whatsapp.value


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
