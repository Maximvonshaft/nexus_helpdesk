from __future__ import annotations

import json
import re
from dataclasses import asdict
from typing import Any

from sqlalchemy.orm import Session

from ..enums import ConversationState, EventType, MessageStatus, NoteVisibility, SourceChannel, TicketStatus
from ..models import Ticket, TicketComment, TicketEvent, TicketOutboundMessage
from ..settings import get_settings
from ..utils.time import utc_now
from ..webchat_models import WebchatConversation, WebchatMessage
from .outbound_safety import evaluate_outbound_safety
from .sla_service import evaluate_sla, update_first_response
from .webchat_ai_service import AI_AUTHOR_LABEL, process_webchat_ai_reply_job as _legacy_process_webchat_ai_reply_job

settings = get_settings()

HIGH_RISK_TERMS = (
    "refund", "compensation", "lost", "damaged", "customs", " tax ", "claim", "legal", "pod",
    "proof of delivery", "delivered but not received", "address change", "change address", "complaint",
    "赔偿", "赔付", "退款", "丢件", "破损", "海关", "清关", "签收未收到", "改地址", "投诉", "索赔",
)
TRACKING_HINT_RE = re.compile(r"\b([A-Z0-9]{8,30})\b", re.IGNORECASE)
BANNED_PUBLIC_TERMS = (
    "OpenClaw", "MCP", "internal", "prompt", "system prompt", "developer message", "tool", "debug", "stack trace",
)


def _is_chinese(text: str | None) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in (text or ""))


def _has_high_risk_intent(text: str | None) -> bool:
    normalized = f" {(text or '').lower()} "
    return any(term.lower() in normalized for term in HIGH_RISK_TERMS)


def _looks_like_tracking_request(text: str | None) -> bool:
    normalized = (text or "").lower()
    keywords = ("tracking", "track", "parcel", "package", "shipment", "delivery", "where is", "order", "单号", "运单", "物流", "包裹", "快递", "派送", "签收")
    return any(keyword in normalized for keyword in keywords)


def _has_tracking_number(ticket: Ticket, visitor_message: WebchatMessage) -> bool:
    if (ticket.tracking_number or "").strip():
        return True
    return bool(TRACKING_HINT_RE.search(visitor_message.body or ""))


def _safe_ack_body(ticket: Ticket, visitor_message: WebchatMessage) -> str:
    body = visitor_message.body or ""
    chinese = _is_chinese(body)
    if _has_high_risk_intent(body):
        return "您好，我是 Speedy，已收到您的消息。客服专员会核查后在这里回复您。" if chinese else "Hi, this is Speedy. I’ve received your message. A support specialist will review it and reply here shortly."
    if _looks_like_tracking_request(body) and not _has_tracking_number(ticket, visitor_message):
        return "您好，我是 Speedy。请提供您的运单号，客服专员会帮您核查并在这里回复您。" if chinese else "Hi, this is Speedy. Please share your tracking number, and a support specialist will review it and reply here."
    if _looks_like_tracking_request(body):
        return "您好，我是 Speedy，已收到您的运单信息。客服专员会核查后在这里回复您。" if chinese else "Hi, this is Speedy. I’ve received your tracking details. A support specialist will review them and reply here."
    return "您好，我是 Speedy，已收到您的消息。请告诉我您需要什么帮助。" if chinese else "Hi, this is Speedy. I’ve received your message. How can I help you today?"


def _sanitize_public_reply(text: str) -> str:
    cleaned = (text or "").strip()
    for term in BANNED_PUBLIC_TERMS:
        cleaned = re.sub(re.escape(term), "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned[:1200]


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


def _write_safe_agent_reply(db: Session, *, conversation: WebchatConversation, ticket: Ticket, visitor_message: WebchatMessage, body: str, reason: str) -> dict[str, Any]:
    final_body = _sanitize_public_reply(body)
    decision = evaluate_outbound_safety(ticket, final_body, source="webchat_safe_ack", has_fact_evidence=False)
    final_body = decision.normalized_body
    safety_payload = asdict(decision)
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
        provider_status="webchat_safe_ack_delivered",
        error_message=reason,
        created_by=None,
        sent_at=utc_now(),
        max_retries=0,
        failure_code=None,
        failure_reason=None,
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
        note="Webchat safe acknowledgement sent",
        payload_json=json.dumps({
            "public_conversation_id": conversation.public_id,
            "conversation_id": conversation.id,
            "visitor_message_id": visitor_message.id,
            "webchat_message_id": message.id,
            "reply_source": reason,
            "safety": safety_payload,
        }, ensure_ascii=False),
    ))
    evaluate_sla(ticket, db)
    return {"status": "done", "message_id": message.id, "fallback": True, "reply_source": reason, "fallback_reason": reason}


def process_webchat_ai_reply_job(db: Session, *, conversation_id: int, ticket_id: int, visitor_message_id: int) -> dict[str, Any]:
    conversation, ticket, visitor_message = _load_context(db, conversation_id=conversation_id, ticket_id=ticket_id, visitor_message_id=visitor_message_id)
    if _agent_reply_exists(db, conversation=conversation, visitor_message=visitor_message):
        return {"status": "skipped", "reason": "agent_reply_already_exists"}

    mode = (settings.webchat_ai_auto_reply_mode or "safe_ack").lower()
    if mode == "off":
        db.add(TicketEvent(
            ticket_id=ticket.id,
            actor_id=None,
            event_type=EventType.internal_note_added,
            note="Webchat AI auto reply skipped because WEBCHAT_AI_AUTO_REPLY_MODE=off",
            payload_json=json.dumps({"conversation_id": conversation.id, "visitor_message_id": visitor_message.id}, ensure_ascii=False),
        ))
        return {"status": "skipped", "reason": "webchat_ai_auto_reply_off"}

    if mode == "safe_ack":
        return _write_safe_agent_reply(db, conversation=conversation, ticket=ticket, visitor_message=visitor_message, body=_safe_ack_body(ticket, visitor_message), reason="webchat_safe_ack_mode")

    if mode == "safe_ai" and _has_high_risk_intent(visitor_message.body):
        return _write_safe_agent_reply(db, conversation=conversation, ticket=ticket, visitor_message=visitor_message, body=_safe_ack_body(ticket, visitor_message), reason="webchat_safe_ai_high_risk_fallback")

    return _legacy_process_webchat_ai_reply_job(db, conversation_id=conversation_id, ticket_id=ticket_id, visitor_message_id=visitor_message_id)
