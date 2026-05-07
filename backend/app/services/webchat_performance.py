from __future__ import annotations

import json
import os
from datetime import timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..enums import ConversationState, UserRole
from ..models import Ticket, User
from ..utils.time import utc_now
from ..webchat_models import WebchatConversation, WebchatMessage
from .permissions import CAP_TICKET_READ, resolve_capabilities

DEFAULT_POLL_LIMIT = 50
MAX_POLL_LIMIT = 100
DEFAULT_LAST_SEEN_WRITE_INTERVAL_SECONDS = 60


def _int_env(name: str, default: int, *, minimum: int = 0, maximum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(value, maximum)
    return value


def webchat_poll_interval_ms() -> int:
    return _int_env("WEBCHAT_POLL_INTERVAL_MS", 10000, minimum=4000, maximum=60000)


def webchat_last_seen_write_interval_seconds() -> int:
    return _int_env(
        "WEBCHAT_LAST_SEEN_WRITE_INTERVAL_SECONDS",
        DEFAULT_LAST_SEEN_WRITE_INTERVAL_SECONDS,
        minimum=0,
        maximum=3600,
    )


def _ensure_aware_utc(value):
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _should_touch_last_seen(last_seen_at) -> bool:
    interval = webchat_last_seen_write_interval_seconds()
    if interval <= 0:
        return True
    now = _ensure_aware_utc(utc_now())
    last_seen = _ensure_aware_utc(last_seen_at)
    if last_seen is None or now is None:
        return True
    return (now - last_seen).total_seconds() >= interval


def _loads_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


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
        "delivery_status": getattr(row, "delivery_status", None) or "sent",
        "action_status": getattr(row, "action_status", None),
        "author_label": row.author_label,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def list_public_messages_throttled(
    db: Session,
    conversation: WebchatConversation,
    *,
    after_id: int | None = None,
    limit: int = DEFAULT_POLL_LIMIT,
) -> dict[str, Any]:
    safe_limit = max(1, min(limit or DEFAULT_POLL_LIMIT, MAX_POLL_LIMIT))
    query = db.query(WebchatMessage).filter(WebchatMessage.conversation_id == conversation.id)
    if after_id is not None:
        query = query.filter(WebchatMessage.id > max(0, after_id))
    rows = query.order_by(WebchatMessage.id.asc()).limit(safe_limit + 1).all()
    has_more = len(rows) > safe_limit
    rows = rows[:safe_limit]

    last_seen_touched = False
    if _should_touch_last_seen(getattr(conversation, "last_seen_at", None)):
        now = utc_now()
        conversation.last_seen_at = now
        conversation.updated_at = now
        db.flush()
        last_seen_touched = True

    return {
        "conversation_id": conversation.public_id,
        "status": conversation.status,
        "messages": [_message_read(row) for row in rows],
        "has_more": has_more,
        "next_after_id": rows[-1].id if rows else after_id,
        "last_seen_touched": last_seen_touched,
    }


def _assert_ticket_read(user: User, db: Session) -> set[str]:
    capabilities = resolve_capabilities(user, db)
    if CAP_TICKET_READ not in capabilities:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Ticket not visible for current user")
    return capabilities


def _ticket_visible_from_preloaded(user: User, ticket: Ticket, capabilities: set[str]) -> bool:
    _ = capabilities
    if user.role in {UserRole.admin, UserRole.manager, UserRole.auditor}:
        return True
    if ticket.assignee_id == user.id:
        return True
    if user.team_id and ticket.team_id == user.team_id:
        return True
    return False


def admin_list_conversations_optimized(db: Session, current_user: User, *, limit: int = 50) -> list[dict[str, Any]]:
    capabilities = _assert_ticket_read(current_user, db)
    safe_limit = max(1, min(int(limit or 50), 100))

    latest_message_ids = (
        db.query(
            WebchatMessage.conversation_id.label("conversation_id"),
            func.max(WebchatMessage.id).label("last_message_id"),
        )
        .group_by(WebchatMessage.conversation_id)
        .subquery()
    )

    rows = (
        db.query(WebchatConversation, Ticket, WebchatMessage)
        .join(Ticket, Ticket.id == WebchatConversation.ticket_id)
        .outerjoin(latest_message_ids, latest_message_ids.c.conversation_id == WebchatConversation.id)
        .outerjoin(WebchatMessage, WebchatMessage.id == latest_message_ids.c.last_message_id)
        .order_by(WebchatConversation.updated_at.desc(), WebchatConversation.id.desc())
        .limit(safe_limit)
        .all()
    )

    items: list[dict[str, Any]] = []
    for conversation, ticket, last_message in rows:
        if not _ticket_visible_from_preloaded(current_user, ticket, capabilities):
            continue
        status_value = ticket.status.value if hasattr(ticket.status, "value") else str(ticket.status)
        state_value = ticket.conversation_state.value if hasattr(ticket.conversation_state, "value") else str(ticket.conversation_state)
        items.append({
            "conversation_id": conversation.public_id,
            "ticket_id": conversation.ticket_id,
            "ticket_no": ticket.ticket_no,
            "title": ticket.title,
            "status": status_value,
            "visitor_name": conversation.visitor_name,
            "visitor_email": conversation.visitor_email,
            "visitor_phone": conversation.visitor_phone,
            "origin": conversation.origin,
            "page_url": conversation.page_url,
            "last_seen_at": conversation.last_seen_at.isoformat() if conversation.last_seen_at else None,
            "updated_at": conversation.updated_at.isoformat() if conversation.updated_at else None,
            "last_message_type": last_message.message_type if last_message else None,
            "last_action_status": last_message.action_status if last_message else None,
            "needs_human": ticket.conversation_state == ConversationState.human_review_required or bool(ticket.required_action),
            "conversation_state": state_value,
            "required_action": ticket.required_action,
            "ai_pending": bool(getattr(conversation, "active_ai_status", None) in {"queued", "processing", "bridge_calling", "fallback_generating"} and getattr(conversation, "active_ai_turn_id", None)),
            "ai_status": getattr(conversation, "active_ai_status", None),
            "ai_turn_id": getattr(conversation, "active_ai_turn_id", None),
            "ai_pending_for_message_id": getattr(conversation, "active_ai_for_message_id", None),
        })
    return items
