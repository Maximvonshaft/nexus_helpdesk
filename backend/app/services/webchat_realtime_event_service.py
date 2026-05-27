from __future__ import annotations

import hashlib
import json
from datetime import timezone
from typing import Any, Literal

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..models import Ticket, User
from ..utils.time import utc_now
from ..webchat_models import WebchatConversation, WebchatEvent, WebchatHandoffRequest, WebchatMessage
from .permissions import ensure_ticket_visible
from .webchat_handoff_service import serialize_handoff_request

Audience = Literal["admin", "visitor"]

CUSTOMER_VISIBLE_EVENT_TYPES = {
    "message.created",
    "handoff.accepted",
    "handoff.released",
    "ai.resumed",
    "ai_turn.queued",
    "ai_turn.processing",
    "ai_turn.bridge_calling",
    "ai_turn.completed",
    "ai_turn.fallback",
    "ai_turn.failed",
    "ai_turn.timeout",
    "ai_turn.cancelled_by_handoff",
    "ai_turn.suppressed_by_handoff",
    "ai_turn.superseded",
    "webchat_ai_reply_suppressed_stale",
}

QUEUE_REFRESH_EVENT_TYPES = {
    "message.created",
    "handoff.requested",
    "handoff.request_updated",
    "handoff.accepted",
    "handoff.declined",
    "handoff.force_takeover",
    "handoff.released",
    "handoff.agent_reply_sent",
    "ai.resumed",
    "ai_turn.queued",
    "ai_turn.processing",
    "ai_turn.bridge_calling",
    "ai_turn.completed",
    "ai_turn.fallback",
    "ai_turn.failed",
    "ai_turn.timeout",
    "ai_turn.cancelled_by_handoff",
    "ai_turn.suppressed_by_handoff",
}


def _loads_json(value: str | None) -> Any:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _ensure_aware_utc(value):
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _message_read(row: WebchatMessage, *, audience: Audience) -> dict[str, Any]:
    payload = {
        "id": row.id,
        "direction": row.direction,
        "body": row.body,
        "body_text": getattr(row, "body_text", None) or row.body,
        "message_type": getattr(row, "message_type", None) or "text",
        "payload_json": _loads_json(getattr(row, "payload_json", None)),
        "metadata_json": _loads_json(getattr(row, "metadata_json", None)),
        "client_message_id": getattr(row, "client_message_id", None),
        "ai_turn_id": getattr(row, "ai_turn_id", None),
        "delivery_status": getattr(row, "delivery_status", None) or "sent",
        "action_status": getattr(row, "action_status", None),
        "author_label": row.author_label,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
    if audience == "admin":
        payload["author_user_id"] = getattr(row, "author_user_id", None)
    return payload


def _sanitize_payload(raw_payload: dict[str, Any], *, audience: Audience) -> dict[str, Any]:
    if audience == "admin":
        return dict(raw_payload)
    redacted = dict(raw_payload)
    for key in ("actor_id", "author_user_id", "assigned_agent_id", "accepted_by_user_id", "forced_by_user_id"):
        redacted.pop(key, None)
    return redacted


def validate_visitor_conversation(db: Session, *, public_id: str, visitor_token: str | None) -> WebchatConversation:
    conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == public_id).first()
    if not conversation or not visitor_token:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="webchat conversation not found or invalid visitor token")
    if _hash_token(visitor_token) != conversation.visitor_token_hash:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="webchat conversation not found or invalid visitor token")
    expires_at = _ensure_aware_utc(getattr(conversation, "visitor_token_expires_at", None))
    now = _ensure_aware_utc(utc_now())
    if expires_at is not None and now is not None and expires_at <= now:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="webchat conversation not found or invalid visitor token")
    return conversation


def validate_agent_conversation(
    db: Session,
    *,
    current_user: User,
    ticket_id: int | None = None,
    public_id: str | None = None,
) -> tuple[WebchatConversation, Ticket]:
    query = db.query(WebchatConversation)
    if ticket_id is not None:
        query = query.filter(WebchatConversation.ticket_id == ticket_id)
    elif public_id:
        query = query.filter(WebchatConversation.public_id == public_id)
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="conversation_id or ticket_id is required")
    conversation = query.first()
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="webchat conversation not found")
    ticket = db.query(Ticket).filter(Ticket.id == conversation.ticket_id).first()
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ticket not found")
    ensure_ticket_visible(current_user, ticket, db)
    return conversation, ticket


def event_envelope(
    db: Session,
    row: WebchatEvent,
    *,
    audience: Audience,
    current_user: User | None = None,
) -> dict[str, Any] | None:
    if audience == "visitor" and row.event_type not in CUSTOMER_VISIBLE_EVENT_TYPES:
        return None
    conversation = db.query(WebchatConversation).filter(WebchatConversation.id == row.conversation_id).first()
    if conversation is None:
        return None
    payload = _sanitize_payload(_loads_json(row.payload_json), audience=audience)
    envelope: dict[str, Any] = {
        "type": row.event_type,
        "event_id": row.id,
        "conversation_id": conversation.public_id,
        "webchat_conversation_id": row.conversation_id,
        "ticket_id": row.ticket_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "payload": payload,
    }
    message_id = payload.get("message_id")
    if row.event_type == "message.created" and message_id:
        message = (
            db.query(WebchatMessage)
            .filter(WebchatMessage.id == int(message_id), WebchatMessage.conversation_id == row.conversation_id)
            .first()
        )
        if message is not None:
            envelope["message"] = _message_read(message, audience=audience)
    handoff_request_id = payload.get("handoff_request_id") or getattr(conversation, "current_handoff_request_id", None)
    if audience == "admin" and current_user is not None and handoff_request_id:
        handoff = db.query(WebchatHandoffRequest).filter(WebchatHandoffRequest.id == int(handoff_request_id)).first()
        ticket = db.query(Ticket).filter(Ticket.id == row.ticket_id).first()
        if handoff is not None and ticket is not None:
            envelope["handoff"] = serialize_handoff_request(db, handoff, current_user=current_user, conversation=conversation, ticket=ticket)
    return envelope


def list_conversation_event_envelopes(
    db: Session,
    *,
    conversation_id: int,
    after_id: int = 0,
    limit: int = 50,
    audience: Audience,
    current_user: User | None = None,
) -> list[dict[str, Any]]:
    rows = (
        db.query(WebchatEvent)
        .filter(WebchatEvent.conversation_id == conversation_id, WebchatEvent.id > max(0, int(after_id or 0)))
        .order_by(WebchatEvent.id.asc())
        .limit(max(1, min(int(limit or 50), 100)))
        .all()
    )
    envelopes: list[dict[str, Any]] = []
    for row in rows:
        envelope = event_envelope(db, row, audience=audience, current_user=current_user)
        if envelope is not None:
            envelopes.append(envelope)
    return envelopes


def list_admin_queue_event_envelopes(
    db: Session,
    *,
    current_user: User,
    after_id: int = 0,
    limit: int = 50,
) -> list[dict[str, Any]]:
    rows = (
        db.query(WebchatEvent)
        .filter(WebchatEvent.id > max(0, int(after_id or 0)), WebchatEvent.event_type.in_(QUEUE_REFRESH_EVENT_TYPES))
        .order_by(WebchatEvent.id.asc())
        .limit(max(1, min(int(limit or 50), 100)) * 2)
        .all()
    )
    envelopes: list[dict[str, Any]] = []
    for row in rows:
        ticket = db.query(Ticket).filter(Ticket.id == row.ticket_id).first()
        if ticket is None:
            continue
        try:
            ensure_ticket_visible(current_user, ticket, db)
        except HTTPException:
            continue
        envelope = event_envelope(db, row, audience="admin", current_user=current_user)
        if envelope is not None:
            envelopes.append(envelope)
        if len(envelopes) >= max(1, min(int(limit or 50), 100)):
            break
    return envelopes
