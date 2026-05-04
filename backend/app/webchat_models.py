from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, event
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
    # Fast-read AI runtime snapshot. These fields are cache values, not the
    # source of truth, so keep them as plain indexed ids to avoid circular FKs.
    active_ai_turn_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    active_ai_status: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    active_ai_for_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    active_ai_context_cutoff_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    next_ai_turn_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    active_ai_started_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    active_ai_updated_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
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


class WebchatAITurn(Base):
    """Durable source of truth for one AI reply turn in a WebChat conversation."""

    __tablename__ = "webchat_ai_turns"
    __table_args__ = (
        UniqueConstraint("trigger_message_id", name="uq_webchat_ai_turn_trigger_message"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("webchat_conversations.id"), index=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    trigger_message_id: Mapped[int] = mapped_column(ForeignKey("webchat_messages.id"), index=True)
    latest_visitor_message_id: Mapped[Optional[int]] = mapped_column(ForeignKey("webchat_messages.id"), nullable=True, index=True)
    context_cutoff_message_id: Mapped[Optional[int]] = mapped_column(ForeignKey("webchat_messages.id"), nullable=True, index=True)
    job_id: Mapped[Optional[int]] = mapped_column(ForeignKey("background_jobs.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(40), default="queued", index=True)
    status_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reply_message_id: Mapped[Optional[int]] = mapped_column(ForeignKey("webchat_messages.id"), nullable=True, index=True)
    reply_source: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    fallback_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    fact_gate_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    bridge_elapsed_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    bridge_timeout_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    superseded_by_turn_id: Mapped[Optional[int]] = mapped_column(ForeignKey("webchat_ai_turns.id"), nullable=True, index=True)
    is_public_reply_allowed: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now, index=True)


class WebchatEvent(Base):
    """Durable WebChat event log for future SSE, auditing, and runtime observability."""

    __tablename__ = "webchat_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("webchat_conversations.id"), index=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
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
