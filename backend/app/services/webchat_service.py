from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
from dataclasses import asdict
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from ..enums import ConversationState, EventType, MessageStatus, NoteVisibility, SourceChannel, TicketPriority, TicketSource, TicketStatus
from ..models import Customer, Ticket, TicketComment, TicketEvent, TicketOutboundMessage, User
from ..utils.time import utc_now
from ..settings import get_settings
from ..webchat_models import WebchatConversation, WebchatMessage
from .outbound_safety import evaluate_outbound_safety, format_safety_reasons
from .permissions import ensure_ticket_visible
from .sla_service import update_first_response, evaluate_sla
from .ticket_service import generate_ticket_no, get_ticket_or_404
from .background_jobs import enqueue_webchat_ai_reply_job
WEBCHAT_LOGGER = logging.getLogger("nexusdesk")

MAX_MESSAGE_CHARS = 2000
MAX_FIELD_CHARS = 300
MAX_URL_CHARS = 700
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_MESSAGES = 20
_RATE_BUCKETS: dict[str, list[float]] = {}


def _clip(value: str | None, limit: int = MAX_FIELD_CHARS) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).strip().split())
    if not cleaned:
        return None
    return cleaned[:limit]


def _clip_body(value: str | None) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="message body is required")
    if len(cleaned) > MAX_MESSAGE_CHARS:
        raise HTTPException(status_code=413, detail=f"message body exceeds {MAX_MESSAGE_CHARS} characters")
    return cleaned


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_public_id() -> str:
    return f"wc_{secrets.token_urlsafe(18).replace('-', '').replace('_', '')[:24]}"


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def _origin_from_request(request: Request, explicit_origin: str | None = None) -> str | None:
    origin = explicit_origin or request.headers.get("origin")
    if origin:
        return _clip(origin, 255)
    referer = request.headers.get("referer")
    if not referer:
        return None
    parsed = urlparse(referer)
    if not parsed.scheme or not parsed.netloc:
        return None
    return _clip(f"{parsed.scheme}://{parsed.netloc}", 255)


def _rate_limit_key(request: Request, conversation_id: str | None, tenant_key: str) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    ip = forwarded or (request.client.host if request.client else "unknown")
    return f"{tenant_key}:{conversation_id or 'init'}:{ip}"


def _enforce_rate_limit(request: Request, conversation_id: str | None, tenant_key: str) -> None:
    now = time.time()
    key = _rate_limit_key(request, conversation_id, tenant_key)
    bucket = [ts for ts in _RATE_BUCKETS.get(key, []) if now - ts < RATE_LIMIT_WINDOW_SECONDS]
    if len(bucket) >= RATE_LIMIT_MAX_MESSAGES:
        raise HTTPException(status_code=429, detail="too many webchat requests")
    bucket.append(now)
    _RATE_BUCKETS[key] = bucket


def _validate_token(conversation: WebchatConversation, token: str | None) -> None:
    if not token or _hash_token(token) != conversation.visitor_token_hash:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid webchat visitor token")


def _message_read(row: WebchatMessage) -> dict[str, Any]:
    return {
        "id": row.id,
        "direction": row.direction,
        "body": row.body,
        "author_label": row.author_label,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def create_or_resume_conversation(db: Session, payload: Any, request: Request) -> dict[str, Any]:
    tenant_key = _clip(getattr(payload, "tenant_key", None) or "default", 120) or "default"
    channel_key = _clip(getattr(payload, "channel_key", None) or "default", 120) or "default"
    public_id = _clip(getattr(payload, "conversation_id", None), 64)
    visitor_token = getattr(payload, "visitor_token", None)
    _enforce_rate_limit(request, public_id, tenant_key)

    if public_id:
        existing = db.query(WebchatConversation).filter(WebchatConversation.public_id == public_id).first()
        if existing:
            _validate_token(existing, visitor_token)
            existing.last_seen_at = utc_now()
            existing.updated_at = utc_now()
            existing.page_url = _clip(getattr(payload, "page_url", None), MAX_URL_CHARS) or existing.page_url
            existing.origin = _origin_from_request(request, getattr(payload, "origin", None)) or existing.origin
            existing.user_agent = _clip(request.headers.get("user-agent"), 300) or existing.user_agent
            db.flush()
            return {
                "conversation_id": existing.public_id,
                "visitor_token": visitor_token,
                "status": existing.status,
                "config": {"poll_interval_ms": 4000, "max_message_chars": MAX_MESSAGE_CHARS},
            }

    token = _new_token()
    public_id = _new_public_id()
    visitor_name = _clip(getattr(payload, "visitor_name", None), 160)
    visitor_email = _clip(getattr(payload, "visitor_email", None), 200)
    visitor_phone = _clip(getattr(payload, "visitor_phone", None), 80)
    visitor_ref = _clip(getattr(payload, "visitor_ref", None), 160)
    origin = _origin_from_request(request, getattr(payload, "origin", None))
    page_url = _clip(getattr(payload, "page_url", None), MAX_URL_CHARS)
    user_agent = _clip(request.headers.get("user-agent"), 300)

    customer = Customer(
        name=visitor_name or visitor_email or visitor_phone or f"Webchat Visitor {public_id[-6:]}",
        email=visitor_email,
        phone=visitor_phone,
        external_ref=visitor_ref or public_id,
    )
    db.add(customer)
    db.flush()

    ticket = Ticket(
        ticket_no=generate_ticket_no(),
        title=f"Webchat inquiry · {customer.name}",
        description="New webchat conversation created from customer website widget.",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.pending_assignment,
        conversation_state=ConversationState.human_owned,
        source_chat_id=public_id,
        preferred_reply_channel=SourceChannel.web_chat.value,
        preferred_reply_contact=visitor_email or visitor_phone or public_id,
        customer_request="Webchat conversation initiated.",
        last_customer_message="Webchat conversation initiated.",
    )
    db.add(ticket)
    db.flush()

    conversation = WebchatConversation(
        public_id=public_id,
        visitor_token_hash=_hash_token(token),
        tenant_key=tenant_key,
        channel_key=channel_key,
        ticket_id=ticket.id,
        visitor_name=visitor_name,
        visitor_email=visitor_email,
        visitor_phone=visitor_phone,
        visitor_ref=visitor_ref,
        origin=origin,
        page_url=page_url,
        user_agent=user_agent,
        status="open",
        last_seen_at=utc_now(),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db.add(conversation)
    db.flush()

    db.add(TicketEvent(
        ticket_id=ticket.id,
        actor_id=None,
        event_type=EventType.ticket_created,
        note="Webchat conversation created",
        payload_json=json.dumps({"public_conversation_id": public_id, "origin": origin, "page_url": page_url}, ensure_ascii=False),
    ))
    db.flush()

    return {
        "conversation_id": conversation.public_id,
        "visitor_token": token,
        "status": conversation.status,
        "config": {"poll_interval_ms": 4000, "max_message_chars": MAX_MESSAGE_CHARS},
    }





def _webchat_auto_ack_text(visitor_body: str) -> str:
    text = (visitor_body or "").strip().lower()
    parcel_keywords = (
        "tracking", "track", "parcel", "package", "shipment", "order", "delivery",
        "where", "delay", "late",
        "快递", "包裹", "物流", "单号", "运单", "派送", "延误", "签收",
    )
    if any(k in text for k in parcel_keywords):
        return (
            "Thanks, we have received your parcel inquiry. "
            "Our support team will check the shipment details and reply here. "
            "If you have a tracking number, please send it in this chat."
        )
    return (
        "Thanks, we have received your message. "
        "Our support team will reply here as soon as possible."
    )


def _maybe_create_webchat_auto_ack(db: Session, *, conversation: WebchatConversation, visitor_message: WebchatMessage) -> None:
    """Create a safe first-response agent message for public webchat.

    This is acknowledgement-only. It must not claim parcel status, delivery result,
    refund status, or any fact not backed by tools.
    """
    if get_settings().openclaw_bridge_enabled:
        return

    existing_agent = (
        db.query(WebchatMessage.id)
        .filter(
            WebchatMessage.conversation_id == conversation.id,
            WebchatMessage.direction == "agent",
        )
        .first()
    )
    if existing_agent:
        return

    row = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=conversation.ticket_id,
        direction="agent",
        body=_webchat_auto_ack_text(visitor_message.body),
        author_label="NexusDesk Assistant",
    )
    db.add(row)

def add_visitor_message(db: Session, public_id: str, visitor_token: str | None, body: str, request: Request) -> dict[str, Any]:
    conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == public_id).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="webchat conversation not found")
    _validate_token(conversation, visitor_token)
    _enforce_rate_limit(request, public_id, conversation.tenant_key)
    normalized_body = _clip_body(body)

    message = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=conversation.ticket_id,
        direction="visitor",
        body=normalized_body,
        author_label=conversation.visitor_name or "Visitor",
    )
    db.add(message)

    db.flush()
    try:
        with db.begin_nested():
            _maybe_create_webchat_auto_ack(db, conversation=conversation, visitor_message=message)
            db.flush()
    except Exception as exc:
        WEBCHAT_LOGGER.exception(
            "webchat_auto_ack_failed",
            extra={"event_payload": {"conversation_id": conversation.public_id, "error": str(exc)}},
        )

    ticket = db.query(Ticket).filter(Ticket.id == conversation.ticket_id).first()
    if ticket:
        ticket.last_customer_message = normalized_body
        ticket.customer_request = normalized_body
        ticket.updated_at = utc_now()
        if ticket.status in {TicketStatus.resolved, TicketStatus.closed}:
            ticket.status = TicketStatus.pending_assignment
            ticket.conversation_state = ConversationState.reopened_by_customer
        else:
            ticket.conversation_state = ConversationState.human_owned
        db.add(TicketComment(ticket_id=ticket.id, author_id=None, body=normalized_body, visibility=NoteVisibility.external))
        db.add(TicketEvent(
            ticket_id=ticket.id,
            actor_id=None,
            event_type=EventType.comment_added,
            note="Webchat visitor message received",
            payload_json=json.dumps({"public_conversation_id": public_id}, ensure_ascii=False),
        ))

    conversation.last_seen_at = utc_now()
    conversation.updated_at = utc_now()
    db.flush()

    try:
        enqueue_webchat_ai_reply_job(
            db,
            conversation_id=conversation.id,
            ticket_id=conversation.ticket_id,
            visitor_message_id=message.id,
        )
        db.flush()
    except Exception as exc:
        WEBCHAT_LOGGER.exception(
            "webchat_ai_reply_enqueue_failed",
            extra={"event_payload": {"conversation_id": conversation.id, "ticket_id": conversation.ticket_id, "visitor_message_id": message.id, "error": str(exc)}},
        )

    db.refresh(message)
    return {"ok": True, "message": _message_read(message)}


def list_public_messages(db: Session, public_id: str, visitor_token: str | None) -> dict[str, Any]:
    conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == public_id).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="webchat conversation not found")
    _validate_token(conversation, visitor_token)
    rows = db.query(WebchatMessage).filter(WebchatMessage.conversation_id == conversation.id).order_by(WebchatMessage.created_at.asc(), WebchatMessage.id.asc()).all()
    conversation.last_seen_at = utc_now()
    db.flush()
    return {"conversation_id": conversation.public_id, "status": conversation.status, "messages": [_message_read(row) for row in rows]}


def admin_list_conversations(db: Session, current_user: User, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = (
        db.query(WebchatConversation)
        .order_by(WebchatConversation.updated_at.desc())
        .limit(max(1, min(limit, 100)))
        .all()
    )
    items: list[dict[str, Any]] = []
    for row in rows:
        ticket = db.query(Ticket).filter(Ticket.id == row.ticket_id).first()
        if not ticket:
            continue
        try:
            ensure_ticket_visible(current_user, ticket, db)
        except HTTPException:
            continue
        items.append({
            "conversation_id": row.public_id,
            "ticket_id": row.ticket_id,
            "ticket_no": ticket.ticket_no,
            "title": ticket.title,
            "status": ticket.status.value if hasattr(ticket.status, "value") else str(ticket.status),
            "visitor_name": row.visitor_name,
            "visitor_email": row.visitor_email,
            "visitor_phone": row.visitor_phone,
            "origin": row.origin,
            "page_url": row.page_url,
            "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        })
    return items


def admin_get_thread(db: Session, ticket_id: int, current_user: User) -> dict[str, Any]:
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_visible(current_user, ticket, db)
    conversation = db.query(WebchatConversation).filter(WebchatConversation.ticket_id == ticket.id).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="webchat conversation not found for ticket")
    rows = db.query(WebchatMessage).filter(WebchatMessage.conversation_id == conversation.id).order_by(WebchatMessage.created_at.asc(), WebchatMessage.id.asc()).all()
    return {
        "conversation_id": conversation.public_id,
        "ticket_id": ticket.id,
        "ticket_no": ticket.ticket_no,
        "origin": conversation.origin,
        "page_url": conversation.page_url,
        "visitor": {
            "name": conversation.visitor_name,
            "email": conversation.visitor_email,
            "phone": conversation.visitor_phone,
            "ref": conversation.visitor_ref,
        },
        "messages": [_message_read(row) for row in rows],
    }


def admin_reply(db: Session, ticket_id: int, current_user: User, *, body: str, has_fact_evidence: bool = False, confirm_review: bool = False) -> dict[str, Any]:
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_visible(current_user, ticket, db)
    conversation = db.query(WebchatConversation).filter(WebchatConversation.ticket_id == ticket.id).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="webchat conversation not found for ticket")

    normalized_body = _clip_body(body)
    decision = evaluate_outbound_safety(ticket, normalized_body, source="manual", has_fact_evidence=has_fact_evidence)
    decision_payload = asdict(decision)
    if decision.level == "block":
        raise HTTPException(status_code=400, detail={"message": "Outbound reply blocked by safety gate", "safety": decision_payload})
    if decision.requires_human_review and not confirm_review:
        raise HTTPException(status_code=409, detail={"message": "Outbound reply requires human review confirmation", "safety": decision_payload})

    message = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        direction="agent",
        body=decision.normalized_body,
        author_label=current_user.display_name,
        safety_level=decision.level,
        safety_reasons_json=json.dumps(decision.reasons, ensure_ascii=False),
    )
    db.add(message)
    db.add(TicketComment(ticket_id=ticket.id, author_id=current_user.id, body=decision.normalized_body, visibility=NoteVisibility.external))
    db.add(TicketOutboundMessage(
        ticket_id=ticket.id,
        channel=SourceChannel.web_chat,
        status=MessageStatus.sent,
        body=decision.normalized_body,
        provider_status="webchat_delivered",
        created_by=current_user.id,
        sent_at=utc_now(),
        max_retries=0,
    ))
    update_first_response(ticket)
    ticket.status = TicketStatus.waiting_customer
    ticket.conversation_state = ConversationState.waiting_customer
    ticket.last_human_update = decision.normalized_body
    ticket.updated_at = utc_now()
    conversation.updated_at = utc_now()
    db.add(TicketEvent(
        ticket_id=ticket.id,
        actor_id=current_user.id,
        event_type=EventType.outbound_sent,
        note="Webchat agent reply sent",
        payload_json=json.dumps({
            "public_conversation_id": conversation.public_id,
            "safety_level": decision.level,
            "safety_reasons": decision.reasons,
            "safety_reason_text": format_safety_reasons(decision),
        }, ensure_ascii=False),
    ))
    evaluate_sla(ticket, db)
    db.flush()
    db.refresh(message)
    return {"ok": True, "safety": decision_payload, "message": _message_read(message)}
