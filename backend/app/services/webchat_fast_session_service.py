from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..utils.time import utc_now
from ..webchat_models import WebchatConversation, WebchatMessage

FAST_ORIGIN = "webchat-fast"
FAST_CONTEXT_LIMIT = 10


def clean_fast_context(items: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "visitor").lower()
        role = "agent" if role in {"ai", "assistant", "agent", "bot"} else "visitor"
        text = str(item.get("text") or item.get("body") or item.get("content") or "").strip()
        if text:
            out.append({"role": role, "text": text[:500]})
    return out[-FAST_CONTEXT_LIMIT:]


def merge_fast_context(server_context: list[dict[str, Any]] | None, frontend_context: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in clean_fast_context(server_context) + clean_fast_context(frontend_context):
        key = (item["role"], item["text"])
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged[-FAST_CONTEXT_LIMIT:]


def get_or_create_fast_conversation(db: Session, *, tenant_key: str, channel_key: str, session_id: str) -> WebchatConversation:
    row = db.execute(
        select(WebchatConversation).where(
            WebchatConversation.tenant_key == tenant_key,
            WebchatConversation.channel_key == channel_key,
            WebchatConversation.fast_session_id == session_id,
            WebchatConversation.origin == FAST_ORIGIN,
            WebchatConversation.status == "open",
        ).limit(1)
    ).scalar_one_or_none()
    now = utc_now()
    if row is not None:
        row.last_seen_at = now
        row.updated_at = now
        db.flush()
        return row
    row = WebchatConversation(
        public_id=("wcf_" + str(abs(hash((tenant_key, channel_key, session_id))))[:24]),
        visitor_token_hash=str(abs(hash(("fast", tenant_key, channel_key, session_id)))),
        tenant_key=tenant_key,
        channel_key=channel_key,
        visitor_ref=session_id,
        origin=FAST_ORIGIN,
        status="open",
        fast_session_id=session_id,
        created_at=now,
        updated_at=now,
        last_seen_at=now,
        fast_context_updated_at=now,
    )
    db.add(row)
    db.flush()
    return row


def append_fast_visitor_message(db: Session, *, conversation: WebchatConversation, body: str, client_message_id: str) -> WebchatMessage:
    existing = db.execute(
        select(WebchatMessage).where(
            WebchatMessage.conversation_id == conversation.id,
            WebchatMessage.client_message_id == client_message_id,
        ).limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    msg = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=conversation.ticket_id,
        direction="visitor",
        body=body,
        body_text=body,
        client_message_id=client_message_id,
        author_label="Customer",
    )
    db.add(msg)
    db.flush()
    return msg


def build_fast_server_context(db: Session, *, conversation: WebchatConversation, limit: int = FAST_CONTEXT_LIMIT) -> list[dict[str, str]]:
    rows = db.execute(
        select(WebchatMessage)
        .where(WebchatMessage.conversation_id == conversation.id)
        .order_by(WebchatMessage.id.desc())
        .limit(limit)
    ).scalars().all()
    return clean_fast_context([
        {"role": row.direction, "text": row.body_text or row.body}
        for row in reversed(rows)
        if row.direction in {"visitor", "ai", "agent"}
    ])
