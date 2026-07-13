from __future__ import annotations

import hashlib
import json
import logging
import secrets
from dataclasses import asdict
from datetime import timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from ..enums import (
    ConversationState,
    EventType,
    MessageStatus,
    NoteVisibility,
    SourceChannel,
    TicketPriority,
    TicketSource,
    TicketStatus,
)
from ..models import Customer, Ticket, TicketComment, User
from ..settings import get_settings
from ..utils.time import utc_now
from ..webchat_models import (
    WebchatAITurn,
    WebchatCardAction,
    WebchatConversation,
    WebchatEvent,
    WebchatHandoffRequest,
    WebchatMessage,
)
from ..webchat_schemas import WebChatActionSubmitRequest, WebChatCardPayload
from .customer_visible_message_service import create_customer_visible_message
from .outbound_safety import evaluate_outbound_safety, format_safety_reasons
from .permissions import ensure_ticket_visible
from .server_fact_evidence import resolve_server_fact_evidence
from .sla_service import evaluate_sla, update_first_response
from .ticket_event_writer import TicketEventClass, TicketEventWriter
from .ticket_service import generate_ticket_no, get_ticket_or_404
from .webchat_ai_turn_service import (
    is_ai_suspended_for_handoff,
    safe_write_webchat_event,
)
from .webchat_handoff_service import (
    ensure_can_reply_in_handoff,
    request_webchat_handoff,
    serialize_handoff_request,
)
from .webchat_inbox_read_state import webchat_read_state_payload
from .webchat_public_payload import public_webchat_metadata

WEBCHAT_LOGGER = logging.getLogger("nexusdesk")

RETIRED_WEBCHAT_CARD_TYPE = "quick" + "_replies"
RETIRED_WEBCHAT_ACTION_TYPE = "quick" + "_reply"

MAX_MESSAGE_CHARS = 2000
MAX_FIELD_CHARS = 300
MAX_URL_CHARS = 700
DEFAULT_POLL_LIMIT = 50
MAX_POLL_LIMIT = 100
WEBCHAT_VISITOR_TOKEN_TTL_DAYS = 7
STALE_PUBLIC_HANDOFF_RESUME_AFTER = timedelta(minutes=30)
SENSITIVE_EVENT_KEYS = ("token", "secret", "password", "authorization", "cookie", "credential", "api_key", "session_key")


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


def _reply_channel_value(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "value"):
        value = value.value
    cleaned = str(value).strip().lower()
    return cleaned or None


def _resolve_admin_reply_channel(ticket: Ticket, conversation: WebchatConversation) -> SourceChannel:
    candidates = (
        getattr(ticket, "preferred_reply_channel", None),
        getattr(ticket, "source_channel", None),
        getattr(conversation, "channel_key", None),
    )
    normalized = {_reply_channel_value(value) for value in candidates}
    if SourceChannel.whatsapp.value in normalized or "whatsapp" in normalized:
        return SourceChannel.whatsapp
    return SourceChannel.web_chat


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
        "ai_turn_id": getattr(row, "ai_turn_id", None),
        "author_user_id": getattr(row, "author_user_id", None),
        "delivery_status": getattr(row, "delivery_status", None) or "sent",
        "action_status": getattr(row, "action_status", None),
        "author_label": row.author_label,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _public_message_read(row: WebchatMessage) -> dict[str, Any]:
    payload = _message_read(row)
    payload["metadata_json"] = public_webchat_metadata(payload.get("metadata_json"))
    payload.pop("author_user_id", None)
    return payload


def _ai_turn_read(row: WebchatAITurn) -> dict[str, Any]:
    runtime_trace: dict[str, Any] | None = None
    if row.runtime_trace_json:
        try:
            parsed = json.loads(row.runtime_trace_json)
            runtime_trace = parsed if isinstance(parsed, dict) else None
        except (TypeError, ValueError):
            runtime_trace = None
    return {
        "id": row.id,
        "status": row.status,
        "trigger_message_id": row.trigger_message_id,
        "latest_visitor_message_id": row.latest_visitor_message_id,
        "context_cutoff_message_id": row.context_cutoff_message_id,
        "reply_message_id": row.reply_message_id,
        "reply_source": row.reply_source,
        "fallback_reason": row.fallback_reason,
        "bridge_elapsed_ms": row.bridge_elapsed_ms,
        "runtime_trace": runtime_trace,
    }


def _redact_event_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(marker in normalized for marker in SENSITIVE_EVENT_KEYS):
                redacted[key] = "[redacted]"
            else:
                redacted[key] = _redact_event_payload(item)
        return redacted
    if isinstance(value, list):
        return [_redact_event_payload(item) for item in value]
    return value


def _event_read(row: WebchatEvent) -> dict[str, Any]:
    return {
        "id": row.id,
        "event_type": row.event_type,
        "payload_json": _redact_event_payload(_loads_json(row.payload_json) or {}),
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

    TicketEventWriter.add(
        db,
        ticket_id=ticket.id,
        actor_id=None,
        event_type=EventType.ticket_created,
        event_class=TicketEventClass.INTERNAL_AUDIT,
        note="Webchat conversation created",
        payload={
            "public_conversation_id": public_id,
            "origin": origin,
            "page_url": page_url,
        },
    )
    WEBCHAT_LOGGER.info("webchat_session_created", extra={"event_payload": {"conversation_id": public_id, "ticket_id": ticket.id, "origin": origin}})
    db.flush()

    return {
        "conversation_id": conversation.public_id,
        "visitor_token": token,
        "status": conversation.status,
        "config": {"poll_interval_ms": 4000, "max_message_chars": MAX_MESSAGE_CHARS, "supports_cards": True, "supports_after_id": True},
    }


def _maybe_resume_stale_requested_handoff(
    db: Session,
    *,
    conversation: WebchatConversation,
    ticket: Ticket | None,
    visitor_message: WebchatMessage,
) -> bool:
    if ticket is None:
        return False
    if getattr(conversation, "handoff_status", None) != "requested":
        return False
    if getattr(conversation, "active_agent_id", None):
        return False

    request_row = None
    request_id = getattr(conversation, "current_handoff_request_id", None)
    if request_id:
        request_row = db.query(WebchatHandoffRequest).filter(WebchatHandoffRequest.id == request_id).first()
        if request_row is not None and request_row.status != "requested":
            return False

    stale_anchor = _ensure_aware_utc(
        getattr(request_row, "requested_at", None)
        if request_row is not None
        else getattr(conversation, "ai_suspended_at", None)
    )
    now = _ensure_aware_utc(utc_now())
    if stale_anchor is None or now is None or now - stale_anchor < STALE_PUBLIC_HANDOFF_RESUME_AFTER:
        return False

    if request_row is not None:
        request_row.status = "resumed_ai"
        request_row.closed_at = now
        request_row.decision_note = _clip("auto_resumed_after_stale_requested_handoff", 240)
        request_row.lock_version += 1
        request_row.updated_at = now

    conversation.current_handoff_request_id = None
    conversation.handoff_status = "none"
    conversation.active_agent_id = None
    conversation.ai_suspended = False
    conversation.ai_suspended_at = None
    conversation.ai_suspended_by = None
    conversation.ai_suspended_reason = None
    conversation.takeover_mode = None
    conversation.last_handoff_reason = None
    conversation.updated_at = now

    ticket.required_action = None
    ticket.conversation_state = ConversationState.ai_active
    ticket.updated_at = now

    event_payload = {
        "handoff_request_id": request_id,
        "message_id": visitor_message.id,
        "reason": "stale_requested_handoff",
        "stale_after_seconds": int(STALE_PUBLIC_HANDOFF_RESUME_AFTER.total_seconds()),
    }
    safe_write_webchat_event(
        db,
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        event_type="ai.resumed",
        payload=event_payload,
    )
    TicketEventWriter.add(
        db,
        ticket_id=ticket.id,
        actor_id=None,
        event_type=EventType.conversation_state_changed,
        event_class=TicketEventClass.INTERNAL_AUDIT,
        note="Stale WebChat handoff resumed by AI",
        payload={"public_conversation_id": conversation.public_id, **event_payload},
    )
    db.flush()
    return True


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
    ticket = db.query(Ticket).filter(Ticket.id == conversation.ticket_id).first()
    if ticket:
        ticket.last_customer_message = normalized_body
        ticket.customer_request = normalized_body
        ticket.updated_at = utc_now()
        if ticket.status in {TicketStatus.resolved, TicketStatus.closed}:
            ticket.status = TicketStatus.pending_assignment
            ticket.conversation_state = ConversationState.reopened_by_customer
        elif ticket.conversation_state != ConversationState.human_review_required and not is_ai_suspended_for_handoff(conversation):
            ticket.conversation_state = ConversationState.human_owned
        db.add(TicketComment(ticket_id=ticket.id, author_id=None, body=normalized_body, visibility=NoteVisibility.external))
        TicketEventWriter.add(
            db,
            ticket_id=ticket.id,
            actor_id=None,
            event_type=EventType.comment_added,
            event_class=TicketEventClass.INTERNAL_AUDIT,
            note="Webchat visitor message received",
            payload={
                "public_conversation_id": public_id,
                "webchat_message_id": message.id,
                "client_message_id": normalized_client_id,
            },
        )

    conversation.last_seen_at = utc_now()
    conversation.updated_at = utc_now()
    db.flush()

    _maybe_resume_stale_requested_handoff(db, conversation=conversation, ticket=ticket, visitor_message=message)

    if is_ai_suspended_for_handoff(conversation):
        safe_write_webchat_event(
            db,
            conversation_id=conversation.id,
            ticket_id=conversation.ticket_id,
            event_type="ai_turn.suppressed_by_handoff",
            payload={"message_id": message.id, "reason": getattr(conversation, "ai_suspended_reason", None)},
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
        "messages": [_public_message_read(row) for row in rows],
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
    raw_card_payload = card_payload_raw
    if isinstance(raw_card_payload, str):
        raw_card_payload = _loads_json(raw_card_payload)
    raw_card_type = raw_card_payload.get("card_type") if isinstance(raw_card_payload, dict) else None
    if raw_card_type == RETIRED_WEBCHAT_CARD_TYPE or payload.action_type == RETIRED_WEBCHAT_ACTION_TYPE:
        WEBCHAT_LOGGER.warning(
            "webchat_card_action_rejected",
            extra={
                "event_payload": {
                    "conversation_id": conversation.id,
                    "message_id": payload.message_id,
                    "reason": "retired_card_action",
                }
            },
        )
        raise HTTPException(status_code=410, detail="webchat retired card action")
    try:
        card_payload = WebChatCardPayload.model_validate(raw_card_payload)
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
    db.flush()
    safe_write_webchat_event(
        db,
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        event_type="message.created",
        payload={"message_id": action_message.id, "direction": "action", "message_type": "action"},
    )
    card_message.action_status = "submitted"
    db.add(TicketComment(ticket_id=ticket.id, author_id=None, body=action_text, visibility=NoteVisibility.external))
    TicketEventWriter.add(
        db,
        ticket_id=ticket.id,
        actor_id=None,
        event_type=EventType.comment_added,
        event_class=TicketEventClass.INTERNAL_AUDIT,
        note="Webchat card action submitted",
        payload={
            "public_conversation_id": conversation.public_id,
            "webchat_card_action_id": action.id,
            "external_send": False,
            **action_payload,
        },
    )
    handoff_triggered = payload.action_type == "handoff_request" or card_payload.card_type == "handoff" or payload.action_id == "talk_to_human"
    if handoff_triggered:
        ticket.required_action = "WebChat customer requested human support"
        ticket.status = TicketStatus.in_progress
        ticket.conversation_state = ConversationState.human_review_required
        request_webchat_handoff(
            db,
            conversation=conversation,
            ticket=ticket,
            source="customer_action",
            trigger_type="card_action",
            reason_code="customer_requested_human_support",
            reason_text=selected.label,
            recommended_agent_action="Customer requested human support from the WebChat handoff card.",
            trigger_message_id=action_message.id,
            requested_by_actor_type="visitor",
        )
        TicketEventWriter.add(
            db,
            ticket_id=ticket.id,
            actor_id=None,
            event_type=EventType.conversation_state_changed,
            event_class=TicketEventClass.INTERNAL_AUDIT,
            note="Webchat handoff requested",
            payload={
                "public_conversation_id": conversation.public_id,
                "required_action": ticket.required_action,
                "external_send": False,
            },
        )
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
            **webchat_read_state_payload(db, conversation_id=row.id, user_id=current_user.id),
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
    ai_turn_rows = (
        db.query(WebchatAITurn)
        .filter(WebchatAITurn.conversation_id == conversation.id)
        .order_by(WebchatAITurn.id.desc())
        .limit(20)
        .all()
    )
    event_rows = (
        db.query(WebchatEvent)
        .filter(WebchatEvent.conversation_id == conversation.id)
        .order_by(WebchatEvent.id.desc())
        .limit(30)
        .all()
    )
    handoff_row = db.query(WebchatHandoffRequest).filter_by(id=conversation.current_handoff_request_id).first() if conversation.current_handoff_request_id else None
    return {
        "conversation_id": conversation.public_id,
        "ticket_id": ticket.id,
        "ticket_no": ticket.ticket_no,
        "origin": conversation.origin,
        "page_url": conversation.page_url,
        "status": ticket.status.value if hasattr(ticket.status, "value") else str(ticket.status),
        "conversation_state": ticket.conversation_state.value if hasattr(ticket.conversation_state, "value") else str(ticket.conversation_state),
        "required_action": ticket.required_action,
        "handoff": serialize_handoff_request(db, handoff_row, current_user=current_user, conversation=conversation, ticket=ticket) if handoff_row else None,
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
        "ai_turns": [_ai_turn_read(row) for row in reversed(ai_turn_rows)],
        "events": [_event_read(row) for row in reversed(event_rows)],
        **webchat_read_state_payload(db, conversation_id=conversation.id, user_id=current_user.id),
    }


def admin_reply(
    db: Session,
    ticket_id: int,
    current_user: User,
    *,
    body: str,
    has_fact_evidence: bool = False,
    evidence_reference_id: int | None = None,
    confirm_review: bool = False,
    conversation_public_id: str | None = None,
) -> dict[str, Any]:
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_visible(current_user, ticket, db)
    query = db.query(WebchatConversation).filter(WebchatConversation.ticket_id == ticket.id)
    if conversation_public_id:
        query = query.filter(WebchatConversation.public_id == conversation_public_id)
    conversation = query.first()
    if not conversation:
        raise HTTPException(status_code=404, detail="webchat conversation not found for ticket")
    ensure_can_reply_in_handoff(db, conversation=conversation, ticket=ticket, current_user=current_user)

    normalized_body = _clip_body(body)
    server_evidence = resolve_server_fact_evidence(
        db,
        ticket=ticket,
        conversation=conversation,
        evidence_reference_id=evidence_reference_id,
    )
    # Backwards-compatible parsing only.  The client boolean is never trusted.
    _ = has_fact_evidence
    decision = evaluate_outbound_safety(
        ticket,
        normalized_body,
        source="manual",
        has_fact_evidence=server_evidence.present,
    )
    decision_payload = asdict(decision)
    decision_payload["evidence"] = server_evidence.audit_payload()
    if decision.level == "block":
        raise HTTPException(status_code=400, detail={"message": "Outbound reply blocked by safety gate", "safety": decision_payload})
    if decision.requires_human_review and not confirm_review:
        raise HTTPException(status_code=409, detail={"message": "Outbound reply requires human review confirmation", "safety": decision_payload})

    if ticket.conversation_state == ConversationState.ai_active:
        ticket.conversation_state = ConversationState.human_owned
        ticket.required_action = None
        conversation.handoff_status = "accepted"
        if hasattr(conversation, "accepted_by_user_id"):
            conversation.accepted_by_user_id = current_user.id
        if hasattr(conversation, "accepted_at"):
            conversation.accepted_at = utc_now()

    reply_channel = _resolve_admin_reply_channel(ticket, conversation)
    is_external_reply = reply_channel == SourceChannel.whatsapp
    delivery_status = "queued" if is_external_reply else "sent"
    provider_status = "whatsapp_agent_reply_queued" if is_external_reply else "webchat_delivered"
    outbound_event_type = EventType.outbound_queued if is_external_reply else EventType.outbound_sent
    outbound_event_note = "WhatsApp agent reply queued" if is_external_reply else "Webchat agent reply sent"
    visible_result = create_customer_visible_message(
        db,
        ticket=ticket,
        conversation=conversation,
        channel=reply_channel,
        body=decision.normalized_body,
        origin="human_agent",
        created_by=current_user.id,
        provider_status=provider_status,
        outbound_status=MessageStatus.sent if not is_external_reply else None,
        delivery_status=delivery_status,
        metadata_json=_metadata(generated_by="human_agent", safety_level=decision.level, fact_evidence_present=server_evidence.present, fact_evidence_reference_id=server_evidence.reference_id, fact_evidence_reason=server_evidence.reason, external_send=is_external_reply),
        author_label=current_user.display_name,
        author_user_id=current_user.id,
        safety_level=decision.level,
        safety_reasons_json=json.dumps(decision.reasons, ensure_ascii=False),
        comment_author_id=current_user.id,
        event_type=outbound_event_type,
        event_note=outbound_event_note,
        event_payload={
            "public_conversation_id": conversation.public_id,
            "safety_level": decision.level,
            "safety_reasons": decision.reasons,
            "safety_reason_text": format_safety_reasons(decision),
            "external_send": is_external_reply,
            "reply_channel": reply_channel.value,
            "provider_status": provider_status,
            "case_context_id": server_evidence.reference_id,
            "fact_evidence_present": server_evidence.present,
            "fact_evidence_reason": server_evidence.reason,
        },
    )
    message = visible_result.webchat_message
    outbound_message = visible_result.outbound_message
    if message is None or outbound_message is None:
        raise HTTPException(status_code=500, detail="customer visible reply was not created")
    message.metadata_json = _metadata(
        generated_by="human_agent",
        safety_level=decision.level,
        fact_evidence_present=server_evidence.present,
        fact_evidence_reference_id=server_evidence.reference_id,
        fact_evidence_reason=server_evidence.reason,
        external_send=is_external_reply,
        reply_channel=reply_channel.value,
        outbound_message_id=outbound_message.id,
        provider_status=provider_status,
    )
    update_first_response(ticket)
    ticket.status = TicketStatus.waiting_customer
    ticket.conversation_state = ConversationState.waiting_customer
    ticket.last_human_update = decision.normalized_body
    ticket.updated_at = utc_now()
    conversation.updated_at = utc_now()
    db.flush()
    safe_write_webchat_event(
        db,
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        event_type="message.created",
        payload={"message_id": message.id, "direction": "agent", "author_user_id": current_user.id},
    )
    if getattr(conversation, "current_handoff_request_id", None):
        safe_write_webchat_event(
            db,
            conversation_id=conversation.id,
            ticket_id=ticket.id,
            event_type="handoff.agent_reply_sent",
            payload={"handoff_request_id": conversation.current_handoff_request_id, "message_id": message.id, "actor_id": current_user.id},
        )
    evaluate_sla(ticket, db)
    db.flush()
    WEBCHAT_LOGGER.info("webchat_message_sent", extra={"event_payload": {"conversation_id": conversation.id, "ticket_id": ticket.id, "message_id": message.id, "external_send": is_external_reply, "reply_channel": reply_channel.value}})
    db.refresh(message)
    return {"ok": True, "safety": decision_payload, "message": _message_read(message)}
