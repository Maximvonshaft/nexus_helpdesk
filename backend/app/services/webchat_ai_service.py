from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict
from typing import Any

from sqlalchemy.orm import Session

from ..enums import ConversationState, EventType, MessageStatus, NoteVisibility, SourceChannel, TicketStatus
from ..models import Ticket, TicketComment, TicketEvent, TicketOutboundMessage
from ..settings import get_settings
from ..utils.time import utc_now
from ..webchat_models import WebchatConversation, WebchatMessage
from .openclaw_mcp_client import OpenClawMCPClient, OpenClawMCPError
from .outbound_safety import evaluate_outbound_safety, format_safety_reasons
from .sla_service import evaluate_sla, update_first_response
from .tracking_fact_schema import TrackingFactResult
from .tracking_fact_service import extract_tracking_number, lookup_tracking_fact
from .webchat_fact_gate import evaluate_webchat_fact_gate

LOGGER = logging.getLogger("nexusdesk")
settings = get_settings()
_LAST_AI_REPLY_SOURCE = "fallback"
_LAST_AI_FALLBACK_REASON = None
_LAST_BRIDGE_ELAPSED_MS = None
_LAST_BRIDGE_EFFECTIVE_TIMEOUT_SECONDS = None
_LAST_BRIDGE_WAIT_TIMEOUT_MS = None

AI_AUTHOR_LABEL = "NexusDesk AI Assistant"
MAX_HISTORY_MESSAGES = 12
TRACKING_HINT_RE = re.compile(r"\b([A-Z0-9]{8,30})\b", re.IGNORECASE)

SAFE_REVIEW_FALLBACK = (
    "Thanks for your message. To avoid giving you inaccurate information, I need a support agent to review this request. "
    "Please share your tracking number if you have it, and our team will follow up here."
)
SAFE_TRACKING_REQUIRED_FALLBACK = (
    "Thanks for your message. To help check your shipment, please send your tracking number here. "
    "Once we have it, our support team can review the case and reply in this chat."
)
SAFE_GENERAL_FALLBACK = (
    "Thanks for your message. Our support team is reviewing your request and will reply here as soon as possible."
)


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
        return {"status": "skipped", "reason": "agent_reply_already_exists"}

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

    ai_reply = _generate_ai_reply(ticket=ticket, conversation=conversation, visitor_message=visitor_message, history_rows=history_rows, tracking_fact=tracking_fact)
    reply_source = _LAST_AI_REPLY_SOURCE
    fallback_reason = _LAST_AI_FALLBACK_REASON
    bridge_elapsed_ms = _LAST_BRIDGE_ELAPSED_MS
    bridge_timeout_seconds = getattr(settings, "openclaw_bridge_timeout_seconds", None)
    bridge_effective_timeout_seconds = _LAST_BRIDGE_EFFECTIVE_TIMEOUT_SECONDS
    bridge_wait_timeout_ms = _LAST_BRIDGE_WAIT_TIMEOUT_MS
    sanitized_empty = False
    fact_gate_reason = None
    if not ai_reply:
        reply_source = "fallback"
        fallback_reason = fallback_reason or "empty_ai_reply"
        ai_reply = _fallback_reply_for(ticket=ticket, visitor_message=visitor_message)

    ai_reply = _sanitize_public_ai_reply(ai_reply)

    if not ai_reply.strip():
        fallback_reason = fallback_reason or "sanitizer_empty"
        sanitized_empty = True
        ai_reply = _fallback_reply_for(ticket=ticket, visitor_message=visitor_message)

    decision = evaluate_outbound_safety(ticket, ai_reply, source="webchat_ai", has_fact_evidence=fact_evidence_present)
    final_body = decision.normalized_body
    safety_payload = asdict(decision)

    if decision.level != "allow" or decision.requires_human_review:
        fallback_reason = fallback_reason or format_safety_reasons(decision)
        final_body = _fallback_reply_for(ticket=ticket, visitor_message=visitor_message)
        fallback_decision = evaluate_outbound_safety(ticket, final_body, source="webchat_safe_fallback", has_fact_evidence=False)
        final_body = fallback_decision.normalized_body
        safety_payload = asdict(fallback_decision)
        decision = fallback_decision
        fact_evidence_present = False

    fact_decision = evaluate_webchat_fact_gate(
        final_body,
        fact_evidence_present=fact_evidence_present,
        allow_tracking_status_card=bool(getattr(settings, "webchat_tracking_fact_card_enabled", False)),
    )
    if not fact_decision.allowed:
        fact_gate_reason = fact_decision.reason or "fact_gate_blocked"
        fallback_reason = fallback_reason or fact_gate_reason
        final_body = _fallback_reply_for(ticket=ticket, visitor_message=visitor_message)
        fallback_decision = evaluate_outbound_safety(ticket, final_body, source="webchat_fact_gate_fallback", has_fact_evidence=False)
        final_body = fallback_decision.normalized_body
        safety_payload = asdict(fallback_decision)
        decision = fallback_decision
        fact_evidence_present = False
        LOGGER.info(
            "webchat_fact_gate_blocked",
            extra={"event_payload": {
                "conversation_id": conversation.id,
                "ticket_id": ticket.id,
                "visitor_message_id": visitor_message.id,
                "reason": fact_gate_reason,
            }},
        )

    message = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        direction="agent",
        body=final_body,
        body_text=final_body,
        message_type="text",
        delivery_status="sent",
        metadata_json=_message_metadata(
            generated_by="webchat_ai_safe_fallback" if fallback_reason else "webchat_ai",
            decision_level=decision.level,
            fallback_reason=fallback_reason,
            reply_source=reply_source,
            fact_evidence_present=fact_evidence_present,
            fact_gate_reason=fact_gate_reason,
            bridge_elapsed_ms=bridge_elapsed_ms,
            bridge_timeout_seconds=bridge_timeout_seconds,
            bridge_effective_timeout_seconds=bridge_effective_timeout_seconds,
            bridge_wait_timeout_ms=bridge_wait_timeout_ms,
            sanitized_empty=sanitized_empty,
            **tracking_fact_metadata,
        ),
        author_label=AI_AUTHOR_LABEL,
        safety_level=decision.level,
        safety_reasons_json=json.dumps(safety_payload.get("reasons", []), ensure_ascii=False),
    )
    db.add(message)
    db.flush()

    provider_status = "webchat_ai_delivered" if not fallback_reason else "webchat_ai_safe_fallback"
    db.add(TicketComment(ticket_id=ticket.id, author_id=None, body=final_body, visibility=NoteVisibility.external))
    db.add(TicketOutboundMessage(
        ticket_id=ticket.id,
        channel=SourceChannel.web_chat,
        status=MessageStatus.sent,
        body=final_body,
        provider_status=provider_status,
        error_message=None if not fallback_reason else fallback_reason,
        created_by=None,
        sent_at=utc_now(),
        max_retries=0,
        failure_code=None if not fallback_reason else "safety_review_required",
        failure_reason=None if not fallback_reason else fallback_reason,
    ))

    update_first_response(ticket)
    ticket.status = TicketStatus.waiting_customer
    ticket.conversation_state = ConversationState.waiting_customer
    ticket.last_human_update = final_body
    ticket.updated_at = utc_now()
    conversation.updated_at = utc_now()
    conversation.last_seen_at = utc_now()

    event_payload = {
        "public_conversation_id": conversation.public_id,
        "conversation_id": conversation.id,
        "visitor_message_id": visitor_message.id,
        "webchat_message_id": message.id,
        "safety": safety_payload,
        "fallback_reason": fallback_reason,
        "fact_gate_reason": fact_gate_reason,
        "fact_evidence_present": fact_evidence_present,
        "tracking_fact": tracking_fact_metadata or None,
        "reply_source": reply_source,
        "provider_status": provider_status,
        "external_send": False,
        "bridge_elapsed_ms": bridge_elapsed_ms,
        "bridge_timeout_seconds": bridge_timeout_seconds,
        "bridge_effective_timeout_seconds": bridge_effective_timeout_seconds,
        "bridge_wait_timeout_ms": bridge_wait_timeout_ms,
        "sanitized_empty": sanitized_empty,
    }
    db.add(TicketEvent(
        ticket_id=ticket.id,
        actor_id=None,
        event_type=EventType.outbound_sent,
        note="Webchat AI reply sent",
        payload_json=json.dumps(event_payload, ensure_ascii=False),
    ))
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
                "external_send": False,
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
            "external_send": False,
            "bridge_elapsed_ms": bridge_elapsed_ms,
            "bridge_timeout_seconds": bridge_timeout_seconds,
            "bridge_effective_timeout_seconds": bridge_effective_timeout_seconds,
            "bridge_wait_timeout_ms": bridge_wait_timeout_ms,
            "sanitized_empty": sanitized_empty,
        }},
    )
    return {"status": "done", "message_id": message.id, "fallback": bool(fallback_reason), "reply_source": reply_source, "fallback_reason": fallback_reason, "fact_evidence_present": fact_evidence_present, "bridge_elapsed_ms": bridge_elapsed_ms, "bridge_effective_timeout_seconds": bridge_effective_timeout_seconds, "bridge_wait_timeout_ms": bridge_wait_timeout_ms}


def _maybe_lookup_tracking_fact(*, conversation: WebchatConversation, ticket: Ticket, visitor_message: WebchatMessage, history_rows: list[WebchatMessage]) -> TrackingFactResult | None:
    if not getattr(settings, "webchat_tracking_fact_lookup_enabled", False):
        return None
    history_candidates = [row.body for row in reversed(history_rows)]
    tracking_number = (ticket.tracking_number or "").strip() or extract_tracking_number(visitor_message.body, *history_candidates)
    if not tracking_number:
        return None
    result = lookup_tracking_fact(
        tracking_number=tracking_number,
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        request_id=f"webchat-{conversation.public_id}-{visitor_message.id}",
    )
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
            "failure_reason": result.failure_reason,
        }},
    )
    return result


def _generate_ai_reply(*, ticket: Ticket, conversation: WebchatConversation, visitor_message: WebchatMessage, history_rows: list[WebchatMessage], tracking_fact: TrackingFactResult | None = None) -> str:
    global _LAST_AI_REPLY_SOURCE, _LAST_AI_FALLBACK_REASON, _LAST_BRIDGE_ELAPSED_MS, _LAST_BRIDGE_EFFECTIVE_TIMEOUT_SECONDS, _LAST_BRIDGE_WAIT_TIMEOUT_MS

    _LAST_AI_REPLY_SOURCE = "fallback"
    _LAST_AI_FALLBACK_REASON = None
    _LAST_BRIDGE_ELAPSED_MS = None
    _LAST_BRIDGE_EFFECTIVE_TIMEOUT_SECONDS = None
    _LAST_BRIDGE_WAIT_TIMEOUT_MS = None

    prompt = _build_prompt(ticket=ticket, conversation=conversation, visitor_message=visitor_message, history_rows=history_rows, tracking_fact=tracking_fact)

    if settings.openclaw_bridge_enabled:
        started = time.monotonic()
        try:
            text = _generate_ai_reply_via_bridge(prompt=prompt, conversation=conversation, visitor_message=visitor_message)
            _LAST_BRIDGE_ELAPSED_MS = int((time.monotonic() - started) * 1000)
            text = (text or "").strip()

            if text:
                _LAST_AI_REPLY_SOURCE = "bridge"
                _LAST_AI_FALLBACK_REASON = None
                return text

            _LAST_AI_REPLY_SOURCE = "fallback"
            _LAST_AI_FALLBACK_REASON = "bridge_empty"
            LOGGER.warning(
                "webchat_ai_bridge_runtime_failed",
                extra={"event_payload": {
                    "conversation_id": conversation.id,
                    "ticket_id": ticket.id,
                    "visitor_message_id": visitor_message.id,
                    "reply_source": _LAST_AI_REPLY_SOURCE,
                    "fallback_reason": _LAST_AI_FALLBACK_REASON,
                    "bridge_elapsed_ms": _LAST_BRIDGE_ELAPSED_MS,
                    "bridge_timeout_seconds": settings.openclaw_bridge_timeout_seconds,
                    "bridge_effective_timeout_seconds": _LAST_BRIDGE_EFFECTIVE_TIMEOUT_SECONDS,
                    "bridge_wait_timeout_ms": _LAST_BRIDGE_WAIT_TIMEOUT_MS,
                }},
            )
            return _fallback_reply_for(ticket=ticket, visitor_message=visitor_message)

        except Exception as exc:
            _LAST_BRIDGE_ELAPSED_MS = int((time.monotonic() - started) * 1000)
            error_text = f"{type(exc).__name__}: {exc}".lower()

            if "timed out" in error_text or "timeout" in error_text:
                reason = "bridge_timeout"
            elif "rejected" in error_text:
                reason = "bridge_rejected"
            elif "empty" in error_text:
                reason = "bridge_empty"
            else:
                reason = "bridge_exception"

            _LAST_AI_REPLY_SOURCE = "fallback"
            _LAST_AI_FALLBACK_REASON = reason

            LOGGER.warning(
                "webchat_ai_bridge_runtime_failed",
                extra={"event_payload": {
                    "conversation_id": conversation.id,
                    "ticket_id": ticket.id,
                    "visitor_message_id": visitor_message.id,
                    "reply_source": _LAST_AI_REPLY_SOURCE,
                    "fallback_reason": _LAST_AI_FALLBACK_REASON,
                    "bridge_elapsed_ms": _LAST_BRIDGE_ELAPSED_MS,
                    "bridge_timeout_seconds": settings.openclaw_bridge_timeout_seconds,
                    "bridge_effective_timeout_seconds": _LAST_BRIDGE_EFFECTIVE_TIMEOUT_SECONDS,
                    "bridge_wait_timeout_ms": _LAST_BRIDGE_WAIT_TIMEOUT_MS,
                    "error_type": type(exc).__name__,
                }},
            )
            return _fallback_reply_for(ticket=ticket, visitor_message=visitor_message)

    _LAST_AI_REPLY_SOURCE = "fallback"
    _LAST_AI_FALLBACK_REASON = "bridge_disabled"
    return _fallback_reply_for(ticket=ticket, visitor_message=visitor_message)


def _generate_ai_reply_via_bridge(*, prompt: str, conversation: WebchatConversation, visitor_message: WebchatMessage) -> str:
    global _LAST_BRIDGE_EFFECTIVE_TIMEOUT_SECONDS, _LAST_BRIDGE_WAIT_TIMEOUT_MS

    bridge_url = settings.openclaw_bridge_url.rstrip('/')
    session_key = f"webchat-ai-{conversation.public_id}-{visitor_message.id}"
    configured_timeout_seconds = int(getattr(settings, "openclaw_bridge_timeout_seconds", 20) or 20)
    effective_timeout_seconds = min(max(configured_timeout_seconds, 5), 30)
    wait_timeout_ms = effective_timeout_seconds * 1000
    _LAST_BRIDGE_EFFECTIVE_TIMEOUT_SECONDS = effective_timeout_seconds
    _LAST_BRIDGE_WAIT_TIMEOUT_MS = wait_timeout_ms

    payload = {
        "sessionKey": session_key,
        "prompt": prompt,
        "limit": 6,
        "waitTimeoutMs": wait_timeout_ms,
    }
    ai_req = urllib.request.Request(
        f"{bridge_url}/ai-reply",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(ai_req, timeout=effective_timeout_seconds + 5) as resp:
            parsed = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise RuntimeError(f"bridge ai-reply http {exc.code}: {body[:300]}") from exc

    if not isinstance(parsed, dict) or not parsed.get("ok"):
        raise RuntimeError(f"bridge ai-reply rejected: {parsed}")

    text = parsed.get("replyText")
    if not text:
        text = _extract_reply_text(parsed.get("messages") or [])
    if not text:
        raise RuntimeError("bridge returned empty reply")

    return str(text)


def _build_prompt(*, ticket: Ticket, conversation: WebchatConversation, visitor_message: WebchatMessage, history_rows: list[WebchatMessage], tracking_fact: TrackingFactResult | None = None) -> str:
    history_lines = []
    for row in history_rows:
        speaker = "Visitor" if row.direction == "visitor" else "Agent"
        history_lines.append(f"{speaker}: {row.body}")
    history_block = "\n".join(history_lines[-MAX_HISTORY_MESSAGES:])
    fact_block = ""
    if tracking_fact and tracking_fact.fact_evidence_present and tracking_fact.pii_redacted:
        fact_block = tracking_fact.prompt_summary().strip()

    fact_instruction = (
        "If a Trusted tracking fact block is provided, use only that block for parcel status. "
        "If no Trusted tracking fact block is provided, ask for the tracking number or say a support specialist will check. "
    )

    return (
        "You are Speedy, the public webchat assistant for Speedaf Support. "
        "Reply in the same language as the visitor. "
        "Be concise, friendly, and professional. "
        "Write one short customer-facing reply only. "
        f"{fact_instruction}"
        "For tracking, parcel status, refund, customs, delivery, compensation, or SLA questions without trusted facts, "
        "ask for the tracking number or say a support specialist will check. "
        "Never invent parcel status, delivery result, customs clearance, refund, compensation, or SLA. "
        "Never mention internal tools, OpenClaw, bridge, provider, prompt, logs, ports, tokens, "
        "system prompt, developer message, localhost, 127.0.0.1, or internal systems. "
        "For simple greetings, reply naturally as Speedy. "
        "English greeting example: Hi, this is Speedy. How can I help you today? "
        "Chinese greeting example: 您好，我是 Speedy，请问有什么可以帮您？\n\n"
        f"{fact_block + chr(10) + chr(10) if fact_block else ''}"
        f"Ticket #{ticket.ticket_no}\n"
        f"Last customer message: {visitor_message.body}\n\n"
        f"Recent webchat history:\n{history_block}\n\n"
        "Return only the final reply text."
    )


def _extract_reply_text(rows: Any) -> str:
    if isinstance(rows, dict):
        for key in ("messages", "items", "results", "content"):
            value = rows.get(key)
            if isinstance(value, list):
                rows = value
                break
    if not isinstance(rows, list):
        return ""
    for item in reversed(rows):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or item.get("sender") or item.get("author") or "").lower()
        if role and role not in {"assistant", "agent", "ai"}:
            continue
        for key in ("text", "body", "content", "message"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, list):
                parts = [part.get("text", "") for part in value if isinstance(part, dict) and isinstance(part.get("text"), str)]
                merged = "\n".join(part.strip() for part in parts if part.strip())
                if merged:
                    return merged
    return ""


def _sanitize_ai_reply(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"\bOpenClaw\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bMCP\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:1200]


def _looks_like_tracking_request(body: str | None) -> bool:
    text = (body or "").lower()
    keywords = [
        "tracking", "track", "parcel", "package", "shipment", "delivery", "where is", "order",
        "单号", "运单", "物流", "包裹", "快递", "派送", "签收",
    ]
    return any(keyword in text for keyword in keywords)


def _has_tracking_number(*, ticket: Ticket, visitor_message: WebchatMessage, history_rows: list[WebchatMessage]) -> bool:
    if (ticket.tracking_number or "").strip():
        return True
    for row in [visitor_message, *history_rows]:
        if TRACKING_HINT_RE.search(row.body or ""):
            return True
    return False


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
        r"\bOpenClaw\b",
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


def _is_probably_chinese_text(text: str | None) -> bool:
    text = text or ""
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _speedy_generic_fallback(body: str | None) -> str:
    if _is_probably_chinese_text(body):
        return "您好，我是 Speedy，已收到您的消息。客服专员会尽快查看并在这里回复您。"
    return "Hi, this is Speedy. I’ve received your message. A support specialist will review it and reply here shortly."


def _speedy_tracking_required_fallback(body: str | None) -> str:
    if _is_probably_chinese_text(body):
        return "您好，我是 Speedy。请提供您的运单号，客服专员会帮您核查并在这里回复您。"
    return "Hi, this is Speedy. Please share your tracking number, and a support specialist will review it and reply here."


def _speedy_review_fallback(body: str | None) -> str:
    if _is_probably_chinese_text(body):
        return "您好，我是 Speedy，已收到您的消息。客服专员会尽快核查并在这里回复您。"
    return "Hi, this is Speedy. I’ve received your message. A support specialist will review it and reply here shortly."


def _fallback_reply_for(*, ticket: Ticket, visitor_message: WebchatMessage) -> str:
    body = getattr(visitor_message, "body", "") or ""
    if _looks_like_tracking_request(body) and not _has_tracking_number(ticket=ticket, visitor_message=visitor_message, history_rows=[]):
        return _speedy_tracking_required_fallback(body)
    if _looks_like_tracking_request(body):
        return _speedy_review_fallback(body)
    return _speedy_generic_fallback(body)
