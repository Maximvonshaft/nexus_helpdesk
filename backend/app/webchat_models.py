from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .utils.time import utc_now

UTCDateTime = DateTime(timezone=True)


class WebchatConversation(Base):
    """Public webchat visitor session linked to one NexusDesk ticket.

    The public API exposes only `public_id` and a one-time visitor token. Internal
    numeric ticket ids stay on the authenticated admin side.
    """

    __tablename__ = "webchat_conversations"
    __table_args__ = (
        UniqueConstraint("tenant_key", "channel_key", "public_id", name="uq_webchat_tenant_channel_public"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    visitor_token_hash: Mapped[str] = mapped_column(String(96), index=True)
    visitor_token_expires_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    tenant_key: Mapped[str] = mapped_column(String(120), default="default", index=True)
    channel_key: Mapped[str] = mapped_column(String(120), default="default", index=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    visitor_name: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    visitor_email: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    visitor_phone: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    visitor_ref: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, index=True)
    origin: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    page_url: Mapped[Optional[str]] = mapped_column(String(700), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="open", index=True)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now, index=True)


class WebchatMessage(Base):
    __tablename__ = "webchat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("webchat_conversations.id"), index=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    direction: Mapped[str] = mapped_column(String(24), index=True)  # visitor | agent | ai | system | action
    body: Mapped[str] = mapped_column(Text)
    body_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    message_type: Mapped[str] = mapped_column(String(32), default="text", index=True)
    payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    client_message_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    delivery_status: Mapped[str] = mapped_column(String(32), default="sent", index=True)
    action_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    author_label: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    safety_level: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    safety_reasons_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)


class WebchatCardAction(Base):
    __tablename__ = "webchat_card_actions"
    __table_args__ = (
        UniqueConstraint("conversation_id", "message_id", "action_id", "submitted_by", name="uq_webchat_card_actions_once_per_action"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("webchat_conversations.id"), index=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("webchat_messages.id"), index=True)
    action_id: Mapped[str] = mapped_column(String(80), default="legacy_action", index=True)
    action_type: Mapped[str] = mapped_column(String(64), index=True)
    action_payload_json: Mapped[str] = mapped_column(Text)
    submitted_by: Mapped[str] = mapped_column(String(64), default="visitor", index=True)
    status: Mapped[str] = mapped_column(String(32), default="submitted", index=True)
    ip_hash: Mapped[Optional[str]] = mapped_column(String(96), nullable=True)
    user_agent_hash: Mapped[Optional[str]] = mapped_column(String(96), nullable=True)
    origin: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now, index=True)


def _extract_action_id_from_payload(raw: str | None) -> str:
    if not raw:
        return "legacy_action"
    try:
        parsed = json.loads(raw)
    except Exception:
        return "legacy_action"
    value = parsed.get("action_id") if isinstance(parsed, dict) else None
    if isinstance(value, str) and value.strip():
        return value.strip()[:80]
    return "legacy_action"


@event.listens_for(WebchatCardAction, "before_insert")
def _populate_webchat_card_action_id(mapper, connection, target: WebchatCardAction) -> None:  # noqa: ANN001
    if not getattr(target, "action_id", None) or target.action_id == "legacy_action":
        target.action_id = _extract_action_id_from_payload(target.action_payload_json)


@event.listens_for(WebchatCardAction, "before_update")
def _refresh_webchat_card_action_id(mapper, connection, target: WebchatCardAction) -> None:  # noqa: ANN001
    if not getattr(target, "action_id", None) or target.action_id == "legacy_action":
        target.action_id = _extract_action_id_from_payload(target.action_payload_json)
