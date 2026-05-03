from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import timezone, timedelta
from dataclasses import asdict
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from ..enums import ConversationState, EventType, MessageStatus, NoteVisibility, SourceChannel, TicketPriority, TicketSource, TicketStatus
from ..models import Customer, Ticket, TicketComment, TicketEvent, TicketOutboundMessage, User
from ..utils.time import utc_now
from ..settings import get_settings
from ..webchat_models import WebchatCardAction, WebchatConversation, WebchatMessage
from ..webchat_schemas import WebChatActionSubmitRequest, WebChatCardPayload
from .outbound_safety import evaluate_outbound_safety, format_safety_reasons
from .permissions import ensure_ticket_visible
from .sla_service import update_first_response, evaluate_sla
from .ticket_service import generate_ticket_no, get_ticket_or_404
from .background_jobs import enqueue_webchat_ai_reply_job
from .webchat_card_factory import build_handoff_card, build_quick_replies_card
from .webchat_intent_service import detect_webchat_intent

WEBCHAT_LOGGER = logging.getLogger("nexusdesk")

MAX_MESSAGE_CHARS = 2000
MAX_FIELD_CHARS = 300
MAX_URL_CHARS = 700
DEFAULT_POLL_LIMIT = 50
MAX_POLL_LIMIT = 100
WEBCHAT_VISITOR_TOKEN_TTL_DAYS = 7


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


def _hash_optional(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def _new_public_id() -> str:
    return f"wc_{secrets.token_urlsafe(18).replace('-', '').replace('_', '')[:24]}"


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def _new_token_expiry():
    return utc_now() + timedelta(days=WEBCHAT_VISITOR_TOKEN_TTL_DAYS)


def _ensure_aware_utc(value):
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


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


def _validate_token(conversation: WebchatConversation, token: str | None) -> None:
    if not token or _hash_token(token) != conversation.visitor_token_hash:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid webchat visitor token")
    expires_at = _ensure_aware_utc(getattr(conversation, "visitor_token_expires_at", None))
    now = _ensure_aware_utc(utc_now())
    if expires_at is not None and now is not None and expires_at <= now:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid webchat visitor token")  # visitor token expired


def _loads_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def _metadata(**items: Any) -> str:
    base = {"external_send": False}
    base.update({key: value for key, value in items.items() if value is not None})
    return json.dumps(base, ensure_ascii=False)


def _message_read(row: WebchatMessage) -> dict[str, Any]:
    message_type = getattr(row, "message_type", None) or "text"
    body_text = getattr(row, "body_text", None) or row.body
    return {
        "id": row.id,
        "direction": row.direction,
        "body": row.body,
        "body_text": body_text,
        "message_type": message_type,
        "payload_json": _loads_json(getattr(row, "payload_json", None)),
        "metadata_json": _loads_json(getattr(row, "metadata_json", None)),
        "client_message_id": getattr(row, "client_message_id", None),
        "delivery_status": getattr(row, "delivery_status", None) or "sent",
        "action_status": getattr(row, "action_status", None),
        "author_label": row.author_label,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def create_or_resume_conversation(db: Session, payload: Any, request: Request) -> dict[str, Any]:
    tenant_key = _clip(getattr(payload, "tenant_key", None) or "default", 120) or "default"
    channel_key = _clip(getattr(payload, "channel_key", None) or "default", 120) or "default"
    public_id = _clip(getattr(payload, "conversation_id", None), 64)
    visitor_token = getattr(payload, "visitor_token", None)

    if public_id:
        existing = db.query(WebchatConversation).filter(WebchatConversation.public_id == public_id).first()
        if existing:
            _validate_token(existing, visitor_token)
            existing.last_seen_at = utc_now()
            existing.visitor_token_expires_at = _new_token_expiry()
            existing.updated_at = utc_now()
            existing.page_url = _clip(getattr(payload, "page_url", None), MAX_URL_CHARS) or existing.page_url
            existing.origin = _origin_from_request(request, getattr(payload, "origin", None)) or existing.origin
            existing.user_agent = _clip(request.headers.get("user-agent"), 300) or existing.user_agent
            db.flush()
            WEBCHAT_LOGGER.info("webchat_session_resumed", extra={"event_payload": {"conversation_id": existing.public_id, "ticket_id": existing.ticket_id}})
            return {
                "conversation_id": existing.public_id,
                "visitor_token": visitor_token,
                "status": existing.status,
                "config": {"poll_interval_ms": 4000, "max_message_chars": MAX_MESSAGE_CHARS, "supports_cards": True, "supports_after_id": True},
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
        visitor_token_expires_at=_new_token_expiry(),
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
    WEBCHAT_LOGGER.info("webchat_session_created", extra={"event_payload": {"conversation_id": public_id, "ticket_id": ticket.id, "origin": origin}})
    db.flush()

    return {
        "conversation_id": conversation.public_id,
        "visitor_token": token,
        "status": conversation.status,
        "config": {"poll_interval_ms": 4000, "max_message_chars": MAX_MESSAGE_CHARS, "supports_cards": True, "supports_after_id": True},
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

    body = _webchat_auto_ack_text(visitor_message.body)
    row = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=conversation.ticket_id,
        direction="agent",
        body=body,
        body_text=body,
        message_type="text",
        delivery_status="sent",
        metadata_json=_metadata(generated_by="system", safety_level="ack_only", fallback_reason="local_safe_ack", fact_evidence_present=False),
        author_label="NexusDesk Assistant",
    )
    db.add(row)


def _write_card_message(db: Session, *, conversation: WebchatConversation, ticket: Ticket, visitor_message: WebchatMessage, card: WebChatCardPayload, provider_status: str, intent_metadata: dict[str, Any]) -> WebchatMessage:
    metadata = {
        "generated_by": "system",
        "intent": intent_metadata.get("intent"),
        "confidence": intent_metadata.get("confidence"),
        "safety_level": "handoff" if card.card_type == "handoff" else "structured_guidance",
        "fallback_reason": intent_metadata.get("fallback_reason"),
        "fact_evidence_present": False,
        "recommended_card": card.card_type,
        "source_message_id": visitor_message.id,
    }
    row = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        direction="system",
        body=card.title,
        body_text=card.body or card.title,
        message_type="card",
        payload_json=card.model_dump_json(),
        metadata_json=_metadata(**metadata),
        delivery_status="sent",
        action_status="pending",
        author_label="NexusDesk Assistant",
    )
    db.add(row)
    db.flush()
    db.add(TicketOutboundMessage(
        ticket_id=ticket.id,
        channel=SourceChannel.web_chat,
        status=MessageStatus.sent,
        body=card.title,
        provider_status=provider_status,
        created_by=None,
        sent_at=utc_now(),
        max_retries=0,
        failure_reason="Local WebChat structured card delivered; no external provider send occurred",
    ))
    db.add(TicketEvent(
        ticket_id=ticket.id,
        actor_id=None,
        event_type=EventType.internal_note_added,
        note=f"Webchat {card.card_type} card generated",
        payload_json=json.dumps({
            "public_conversation_id": conversation.public_id,
            "webchat_message_id": row.id,
            "card_id": card.card_id,
            "card_type": card.card_type,
            "provider_status": provider_status,
            "external_send": False,
            "intent": intent_metadata,
        }, ensure_ascii=False),
    ))
    WEBCHAT_LOGGER.info("webchat_card_generated", extra={"event_payload": {"conversation_id": conversation.id, "ticket_id": ticket.id, "message_id": row.id, "card_type": card.card_type}})
    return row


def _maybe_create_structured_card(db: Session, *, conversation: WebchatConversation, ticket: Ticket, visitor_message: WebchatMessage) -> None:
    existing_card = (
        db.query(WebchatMessage.id)
        .filter(
            WebchatMessage.conversation_id == conversation.id,
            WebchatMessage.message_type == "card",
            WebchatMessage.id > visitor_message.id,
        )
        .first()
    )
    if existing_card:
        return
    intent = detect_webchat_intent(visitor_message.body)
    intent_metadata = intent.to_metadata()
    if intent.risk_level == "high" or intent.intent in {"handoff", "complaint", "address_change", "reschedule"}:
        ticket.required_action = "WebChat customer needs human review / handoff"
        ticket.conversation_state = ConversationState.human_review_required
        card = build_handoff_card(reason=f"intent:{intent.intent}")
        _write_card_message(db, conversation=conversation, ticket=ticket, visitor_message=visitor_message, card=card, provider_status="webchat_handoff_ack_delivered", intent_metadata={**intent_metadata, "fallback_reason": "high_risk_or_handoff_intent"})
        WEBCHAT_LOGGER.info("webchat_handoff_triggered", extra={"event_payload": {"conversation_id": conversation.id, "ticket_id": ticket.id, "reason": intent.intent}})
        return
    if intent.recommended_card == "quick_replies" or intent.intent in {"greeting", "tracking", "unknown"}:
        settings = get_settings()
        if settings.webchat_static_quick_replies_mode != "legacy":
            WEBCHAT_LOGGER.info("webchat_static_quick_replies_skipped", extra={"event_payload": {
                "conversation_id": conversation.id,
                "ticket_id": ticket.id,
                "visitor_message_id": visitor_message.id,
                "intent": intent.intent,
                "recommended_card": intent.recommended_card,
                "mode": settings.webchat_static_quick_replies_mode
            }})
            return

        body = "Choose one option below. If this is about a parcel, please share your tracking number."
        if "tracking_number" in intent.missing_fields:
            body = "Please share your tracking number, or choose another option below."
        card = build_quick_replies_card(body=body, intent=intent.intent)
        _write_card_message(db, conversation=conversation, ticket=ticket, visitor_message=visitor_message, card=card, provider_status="webchat_card_delivered", intent_metadata=intent_metadata)


def add_visitor_message(db: Session, public_id: str, visitor_token: str | None, body: str, request: Request, *, client_message_id: str | None = None) -> dict[str, Any]:
    conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == public_id).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="webchat conversation not found")
    _validate_token(conversation, visitor_token)
    normalized_body = _clip_body(body)
    normalized_client_id = _clip(client_message_id, 120)

    if normalized_client_id:
        existing = (
            db.query(WebchatMessage)
            .filter(
                WebchatMessage.conversation_id == conversation.id,
                WebchatMessage.client_message_id == normalized_client_id,
                WebchatMessage.direction == "visitor",
            )
            .first()
        )
        if existing:
            return {"ok": True, "idempotent": True, "message": _message_read(existing)}

    message = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=conversation.ticket_id,
        direction="visitor",
        body=normalized_body,
        body_text=normalized_body,
        message_type="text",
        client_message_id=normalized_client_id,
        delivery_status="sent",
        metadata_json=_metadata(generated_by="visitor", origin=_origin_from_request(request), fact_evidence_present=False),
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
        elif ticket.conversation_state != ConversationState.human_review_required:
            ticket.conversation_state = ConversationState.human_owned
        db.add(TicketComment(ticket_id=ticket.id, author_id=None, body=normalized_body, visibility=NoteVisibility.external))
        db.add(TicketEvent(
            ticket_id=ticket.id,
            actor_id=None,
            event_type=EventType.comment_added,
            note="Webchat visitor message received",
            payload_json=json.dumps({"public_conversation_id": public_id, "webchat_message_id": message.id, "client_message_id": normalized_client_id}, ensure_ascii=False),
        ))
        try:
            _maybe_create_structured_card(db, conversation=conversation, ticket=ticket, visitor_message=message)
        except Exception as exc:
            WEBCHAT_LOGGER.exception("webchat_card_generation_failed", extra={"event_payload": {"conversation_id": conversation.id, "ticket_id": ticket.id, "visitor_message_id": message.id, "error": str(exc)}})

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

    WEBCHAT_LOGGER.info("webchat_message_received", extra={"event_payload": {"conversation_id": conversation.id, "ticket_id": conversation.ticket_id, "message_id": message.id}})
    db.refresh(message)
    return {"ok": True, "message": _message_read(message)}


def list_public_messages(db: Session, public_id: str, visitor_token: str | None, *, after_id: int | None = None, limit: int = DEFAULT_POLL_LIMIT) -> dict[str, Any]:
    conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == public_id).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="webchat conversation not found")
    _validate_token(conversation, visitor_token)
    safe_limit = max(1, min(limit or DEFAULT_POLL_LIMIT, MAX_POLL_LIMIT))
    query = db.query(WebchatMessage).filter(WebchatMessage.conversation_id == conversation.id)
    if after_id is not None:
        query = query.filter(WebchatMessage.id > max(0, after_id))
    rows = query.order_by(WebchatMessage.id.asc()).limit(safe_limit + 1).all()
    has_more = len(rows) > safe_limit
    rows = rows[:safe_limit]
    conversation.last_seen_at = utc_now()
    db.flush()
    WEBCHAT_LOGGER.info("webchat_message_polled", extra={"event_payload": {"conversation_id": conversation.id, "after_id": after_id, "returned": len(rows), "has_more": has_more}})
    return {
        "conversation_id": conversation.public_id,
        "status": conversation.status,
        "messages": [_message_read(row) for row in rows],
        "has_more": has_more,
        "next_after_id": rows[-1].id if rows else after_id,
    }


def submit_card_action(db: Session, public_id: str, visitor_token: str | None, payload: WebChatActionSubmitRequest, request: Request) -> dict[str, Any]:
    conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == public_id).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="webchat conversation not found")
    _validate_token(conversation, visitor_token)
    card_message = db.query(WebchatMessage).filter(WebchatMessage.id == payload.message_id, WebchatMessage.conversation_id == conversation.id).first()
    if not card_message or (card_message.message_type or "text") != "card":
        raise HTTPException(status_code=404, detail="webchat card message not found")
    card_payload_raw = _loads_json(card_message.payload_json)
    try:
        card_payload = WebChatCardPayload.model_validate(card_payload_raw)
    except Exception as exc:
        WEBCHAT_LOGGER.warning("webchat_card_action_rejected", extra={"event_payload": {"conversation_id": conversation.id, "message_id": payload.message_id, "reason": "invalid_stored_card_payload"}})
        raise HTTPException(status_code=400, detail="invalid stored card payload") from exc
    if card_payload.card_id != payload.card_id:
        raise HTTPException(status_code=400, detail="card_id does not match message payload")
    selected = next((item for item in card_payload.actions if item.id == payload.action_id), None)
    if selected is None:
        WEBCHAT_LOGGER.warning("webchat_card_action_rejected", extra={"event_payload": {"conversation_id": conversation.id, "message_id": payload.message_id, "action_id": payload.action_id, "reason": "unknown_action_id"}})
        raise HTTPException(status_code=400, detail="action_id is not allowed for this card")
    if selected.action_type != payload.action_type:
        raise HTTPException(status_code=400, detail="action_type does not match card action")

    ticket = db.query(Ticket).filter(Ticket.id == conversation.ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="ticket not found")
    action_payload = {
        "card_id": payload.card_id,
        "card_type": card_payload.card_type,
        "action_id": payload.action_id,
        "action_type": payload.action_type,
        "label": selected.label,
        "value": selected.value,
        "payload": payload.payload or selected.payload,
    }
    action = WebchatCardAction(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        message_id=card_message.id,
        action_type=payload.action_type,
        action_payload_json=json.dumps(action_payload, ensure_ascii=False),
        submitted_by="visitor",
        status="submitted",
        ip_hash=_hash_optional(request.client.host if request.client else None),
        user_agent_hash=_hash_optional(request.headers.get("user-agent")),
        origin=_origin_from_request(request),
    )
    db.add(action)
    db.flush()
    action_text = f"Visitor selected: {selected.label}"
    action_message = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        direction="action",
        body=action_text,
        body_text=action_text,
        message_type="action",
        payload_json=json.dumps(action_payload, ensure_ascii=False),
        metadata_json=_metadata(generated_by="visitor", action_row_id=action.id, fact_evidence_present=False),
        delivery_status="sent",
        action_status="submitted",
        author_label=conversation.visitor_name or "Visitor",
    )
    db.add(action_message)
    card_message.action_status = "submitted"
    db.add(TicketComment(ticket_id=ticket.id, author_id=None, body=action_text, visibility=NoteVisibility.external))
    db.add(TicketEvent(
        ticket_id=ticket.id,
        actor_id=None,
        event_type=EventType.comment_added,
        note="Webchat card action submitted",
        payload_json=json.dumps({"public_conversation_id": conversation.public_id, "webchat_card_action_id": action.id, "external_send": False, **action_payload}, ensure_ascii=False),
    ))
    handoff_triggered = payload.action_type == "handoff_request" or card_payload.card_type == "handoff" or payload.action_id == "talk_to_human"
    if handoff_triggered:
        ticket.required_action = "WebChat customer requested human support"
        ticket.status = TicketStatus.in_progress
        ticket.conversation_state = ConversationState.human_review_required
        db.add(TicketOutboundMessage(
            ticket_id=ticket.id,
            channel=SourceChannel.web_chat,
            status=MessageStatus.sent,
            body="Human handoff requested in WebChat",
            provider_status="webchat_handoff_ack_delivered",
            created_by=None,
            sent_at=utc_now(),
            max_retries=0,
            failure_reason="Local WebChat handoff acknowledgement; no external provider send occurred",
        ))
        db.add(TicketEvent(
            ticket_id=ticket.id,
            actor_id=None,
            event_type=EventType.conversation_state_changed,
            note="Webchat handoff requested",
            payload_json=json.dumps({"public_conversation_id": conversation.public_id, "required_action": ticket.required_action, "external_send": False}, ensure_ascii=False),
        ))
        WEBCHAT_LOGGER.info("webchat_handoff_triggered", extra={"event_payload": {"conversation_id": conversation.id, "ticket_id": ticket.id, "action_id": action.id}})
    conversation.updated_at = utc_now()
    conversation.last_seen_at = utc_now()
    ticket.updated_at = utc_now()
    db.flush()
    WEBCHAT_LOGGER.info("webchat_card_action_submitted", extra={"event_payload": {"conversation_id": conversation.id, "ticket_id": ticket.id, "action_id": action.id, "action_type": payload.action_type}})
    db.refresh(action_message)
    return {"ok": True, "action_id": action.id, "status": action.status, "message": _message_read(action_message), "handoff_triggered": handoff_triggered}


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
        last_message = db.query(WebchatMessage).filter(WebchatMessage.conversation_id == row.id).order_by(WebchatMessage.id.desc()).first()
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
            "last_message_type": last_message.message_type if last_message else None,
            "last_action_status": last_message.action_status if last_message else None,
            "needs_human": ticket.conversation_state == ConversationState.human_review_required or bool(ticket.required_action),
        })
    return items


def admin_get_thread(db: Session, ticket_id: int, current_user: User) -> dict[str, Any]:
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_visible(current_user, ticket, db)
    conversation = db.query(WebchatConversation).filter(WebchatConversation.ticket_id == ticket.id).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="webchat conversation not found for ticket")
    rows = db.query(WebchatMessage).filter(WebchatMessage.conversation_id == conversation.id).order_by(WebchatMessage.created_at.asc(), WebchatMessage.id.asc()).all()
    actions = db.query(WebchatCardAction).filter(WebchatCardAction.conversation_id == conversation.id).order_by(WebchatCardAction.created_at.asc(), WebchatCardAction.id.asc()).all()
    return {
        "conversation_id": conversation.public_id,
        "ticket_id": ticket.id,
        "ticket_no": ticket.ticket_no,
        "origin": conversation.origin,
        "page_url": conversation.page_url,
        "status": ticket.status.value if hasattr(ticket.status, "value") else str(ticket.status),
        "conversation_state": ticket.conversation_state.value if hasattr(ticket.conversation_state, "value") else str(ticket.conversation_state),
        "required_action": ticket.required_action,
        "visitor": {
            "name": conversation.visitor_name,
            "email": conversation.visitor_email,
            "phone": conversation.visitor_phone,
            "ref": conversation.visitor_ref,
        },
        "messages": [_message_read(row) for row in rows],
        "actions": [{
            "id": action.id,
            "message_id": action.message_id,
            "action_type": action.action_type,
            "status": action.status,
            "payload": _loads_json(action.action_payload_json) or {},
            "submitted_by": action.submitted_by,
            "origin": action.origin,
            "created_at": action.created_at.isoformat() if action.created_at else None,
        } for action in actions],
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
        body_text=decision.normalized_body,
        message_type="text",
        delivery_status="sent",
        metadata_json=_metadata(generated_by="human_agent", safety_level=decision.level, fact_evidence_present=has_fact_evidence),
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
            "external_send": False,
            "provider_status": "webchat_delivered",
        }, ensure_ascii=False),
    ))
    evaluate_sla(ticket, db)
    db.flush()
    WEBCHAT_LOGGER.info("webchat_message_sent", extra={"event_payload": {"conversation_id": conversation.id, "ticket_id": ticket.id, "message_id": message.id, "external_send": False}})
    db.refresh(message)
    return {"ok": True, "safety": decision_payload, "message": _message_read(message)}
