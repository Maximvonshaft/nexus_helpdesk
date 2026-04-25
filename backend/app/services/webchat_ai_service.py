from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict
from typing import Any

from sqlalchemy.orm import Session

from ..enums import ConversationState, EventType, MessageStatus, NoteVisibility, SourceChannel, TicketStatus
from ..models import Ticket, TicketComment, TicketEvent, TicketOutboundMessage
from ..utils.time import utc_now
from ..webchat_models import WebchatConversation, WebchatMessage
from .openclaw_mcp_client import OpenClawMCPClient, OpenClawMCPError
from .outbound_safety import evaluate_outbound_safety, format_safety_reasons
from .sla_service import evaluate_sla, update_first_response

LOGGER = logging.getLogger("nexusdesk")
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

    ai_reply = _generate_ai_reply(ticket=ticket, conversation=conversation, visitor_message=visitor_message, history_rows=history_rows)
    fallback_reason = None
    if not ai_reply:
        fallback_reason = "empty_ai_reply"
        ai_reply = _fallback_reply_for(ticket=ticket, visitor_message=visitor_message)

    decision = evaluate_outbound_safety(ticket, ai_reply, source="ai", has_fact_evidence=False)
    final_body = decision.normalized_body
    safety_payload = asdict(decision)

    if decision.level != "allow" or decision.requires_human_review:
        fallback_reason = fallback_reason or format_safety_reasons(decision)
        final_body = _fallback_reply_for(ticket=ticket, visitor_message=visitor_message)
        fallback_decision = evaluate_outbound_safety(ticket, final_body, source="webchat_safe_fallback", has_fact_evidence=False)
        final_body = fallback_decision.normalized_body
        safety_payload = asdict(fallback_decision)
        decision = fallback_decision

    message = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        direction="agent",
        body=final_body,
        author_label=AI_AUTHOR_LABEL,
        safety_level=decision.level,
        safety_reasons_json=json.dumps(safety_payload.get("reasons", []), ensure_ascii=False),
    )
    db.add(message)
    db.flush()

    db.add(TicketComment(ticket_id=ticket.id, author_id=None, body=final_body, visibility=NoteVisibility.external))
    db.add(TicketOutboundMessage(
        ticket_id=ticket.id,
        channel=SourceChannel.web_chat,
        status=MessageStatus.sent,
        body=final_body,
        provider_status="webchat_ai_delivered" if not fallback_reason else "webchat_ai_safe_fallback",
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

    db.add(TicketEvent(
        ticket_id=ticket.id,
        actor_id=None,
        event_type=EventType.outbound_sent,
        note="Webchat AI reply sent",
        payload_json=json.dumps({
            "public_conversation_id": conversation.public_id,
            "conversation_id": conversation.id,
            "visitor_message_id": visitor_message.id,
            "webchat_message_id": message.id,
            "safety": safety_payload,
            "fallback_reason": fallback_reason,
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
        }},
    )
    return {"status": "done", "message_id": message.id, "fallback": bool(fallback_reason)}


def _generate_ai_reply(*, ticket: Ticket, conversation: WebchatConversation, visitor_message: WebchatMessage, history_rows: list[WebchatMessage]) -> str:
    if _looks_like_tracking_request(visitor_message.body) and not _has_tracking_number(ticket=ticket, visitor_message=visitor_message, history_rows=history_rows):
        return SAFE_TRACKING_REQUIRED_FALLBACK

    prompt = _build_prompt(ticket=ticket, conversation=conversation, visitor_message=visitor_message, history_rows=history_rows)
    try:
        with OpenClawMCPClient() as client:
            session_key = f"webchat-ai-{conversation.public_id}-{visitor_message.id}"
            client.messages_send(session_key, prompt)
            rows = client.messages_read(session_key, limit=6)
        text = _extract_reply_text(rows)
        return _sanitize_ai_reply(text)
    except (OpenClawMCPError, FileNotFoundError) as exc:
        LOGGER.warning(
            "webchat_ai_runtime_failed",
            extra={"event_payload": {
                "conversation_id": conversation.id,
                "ticket_id": ticket.id,
                "visitor_message_id": visitor_message.id,
                "error": str(exc),
            }},
        )
        return _fallback_reply_for(ticket=ticket, visitor_message=visitor_message)


def _build_prompt(*, ticket: Ticket, conversation: WebchatConversation, visitor_message: WebchatMessage, history_rows: list[WebchatMessage]) -> str:
    history_lines = []
    for row in history_rows:
        speaker = "Visitor" if row.direction == "visitor" else "Agent"
        history_lines.append(f"{speaker}: {row.body}")
    history_block = "\n".join(history_lines[-MAX_HISTORY_MESSAGES:])
    return (
        "You are a customer support reply assistant for a public web chat. "
        "Write one short customer-facing reply only. Do not mention OpenClaw, MCP, prompt, policy, or internal systems. "
        "Do not claim parcel delivered, signed, refunded, compensated, customs cleared, or promise arrival times unless exact evidence is provided. "
        "If the customer asks about parcel status and no tracking number is available, ask them to provide the tracking number. "
        "If human verification is needed, say a support agent will review and reply here. "
        "Keep it concise, polite, and useful.\n\n"
        f"Ticket #{ticket.ticket_no}\n"
        f"Conversation public id: {conversation.public_id}\n"
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


def _fallback_reply_for(*, ticket: Ticket, visitor_message: WebchatMessage) -> str:
    if _looks_like_tracking_request(visitor_message.body) and not _has_tracking_number(ticket=ticket, visitor_message=visitor_message, history_rows=[]):
        return SAFE_TRACKING_REQUIRED_FALLBACK
    if _looks_like_tracking_request(visitor_message.body):
        return SAFE_REVIEW_FALLBACK
    return SAFE_GENERAL_FALLBACK
