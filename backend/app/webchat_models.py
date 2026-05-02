from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
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
    visitor_token_expires_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now, index=True)


class WebchatMessage(Base):
    __tablename__ = "webchat_messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "direction", "client_message_id", name="uq_webchat_message_client_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("webchat_conversations.id"), index=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    direction: Mapped[str] = mapped_column(String(24), index=True)  # visitor | agent | system
    client_message_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    body: Mapped[str] = mapped_column(Text)
    author_label: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    safety_level: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    safety_reasons_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
