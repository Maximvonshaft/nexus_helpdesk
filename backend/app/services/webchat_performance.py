from __future__ import annotations

import json
import os
from datetime import timezone
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..utils.time import utc_now
from ..webchat_models import WebchatConversation, WebchatMessage
from .webchat_public_payload import (
    PUBLIC_WEBCHAT_HIDDEN_MESSAGE_TYPES,
    public_webchat_metadata,
)


DEFAULT_POLL_LIMIT = 50
MAX_POLL_LIMIT = 100
DEFAULT_LAST_SEEN_WRITE_INTERVAL_SECONDS = 60


def _int_env(
    name: str,
    default: int,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(value, maximum)
    return value


def webchat_poll_interval_ms() -> int:
    return _int_env(
        "WEBCHAT_POLL_INTERVAL_MS",
        1000,
        minimum=500,
        maximum=60000,
    )


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
    metadata = _loads_json(getattr(row, "metadata_json", None))
    return {
        "id": row.id,
        "direction": row.direction,
        "body": row.body,
        "body_text": body_text,
        "message_type": message_type,
        "payload_json": _loads_json(getattr(row, "payload_json", None)),
        "metadata_json": public_webchat_metadata(metadata),
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
    query = db.query(WebchatMessage).filter(
        WebchatMessage.conversation_id == conversation.id,
        or_(
            WebchatMessage.message_type.is_(None),
            WebchatMessage.message_type.notin_(
                tuple(PUBLIC_WEBCHAT_HIDDEN_MESSAGE_TYPES)
            ),
        ),
    )
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


def admin_list_conversations_optimized(
    db: Session,
    current_user,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Compatibility projection backed by the canonical support list authority."""

    # Local import avoids coupling public WebChat polling startup to the
    # authenticated support API module. No independent query authority remains.
    from app.api.support_conversations import list_support_conversations

    payload = list_support_conversations(
        view="all",
        channel="all",
        q=None,
        limit=max(1, min(int(limit or 50), 100)),
        current_user=current_user,
        db=db,
    )
    items = payload.get("items") if isinstance(payload, dict) else []
    rows: list[dict[str, Any]] = []
    for item in items if isinstance(items, list) else []:
        session_key = str(item.get("session_key") or "")
        public_id = session_key.split(":", 1)[-1]
        rows.append(
            {
                "conversation_id": public_id,
                "ticket_id": item.get("ticket_id"),
                "ticket_no": item.get("ticket_no"),
                "title": item.get("title"),
                "status": item.get("status"),
                "visitor_name": item.get("display_name"),
                "visitor_email": None,
                "visitor_phone": item.get("customer_contact"),
                "origin": None,
                "page_url": None,
                "last_seen_at": item.get("last_seen_at"),
                "updated_at": item.get("updated_at"),
                "last_message_type": None,
                "last_action_status": None,
                "needs_human": bool(item.get("needs_human")),
                "conversation_state": item.get("conversation_state"),
                "required_action": item.get("required_action"),
                "ai_pending": bool(item.get("ai_pending")),
                "ai_status": item.get("ai_status"),
                "ai_turn_id": item.get("ai_turn_id"),
                "ai_pending_for_message_id": item.get(
                    "ai_pending_for_message_id"
                ),
                "current_handoff_request_id": item.get(
                    "handoff_request_id"
                ),
                "handoff_status": item.get("handoff_status") or "none",
                "active_agent_id": item.get("active_agent_id"),
                "ai_suspended": bool(item.get("ai_suspended")),
                "takeover_mode": item.get("takeover_mode"),
                "last_handoff_reason": item.get("last_handoff_reason"),
                "marked_unread": bool(item.get("marked_unread", False)),
                "read_at": item.get("read_at"),
                "last_read_message_id": item.get("last_read_message_id"),
                "pii_minimized": True,
                "deprecated_transport": True,
                "canonical_endpoint": "/api/support/conversations",
            }
        )
    return rows
