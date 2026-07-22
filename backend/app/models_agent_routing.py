from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .utils.time import utc_now


UTCDateTime = DateTime(timezone=True)


class ConversationControl(Base):
    """Conversation-owned lifecycle facts that do not depend on a Ticket row."""

    __tablename__ = "conversation_controls"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", name="uq_conversation_controls_conversation"
        ),
        Index("ix_conversation_controls_customer", "customer_id"),
        Index(
            "ix_conversation_controls_scope",
            "tenant_key",
            "country_code",
            "channel_key",
        ),
        Index("ix_conversation_controls_outcome", "outcome", "closed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("webchat_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )
    tenant_key: Mapped[str] = mapped_column(String(120), nullable=False)
    country_code: Mapped[Optional[str]] = mapped_column(
        String(16), nullable=True, index=True
    )
    channel_key: Mapped[str] = mapped_column(String(120), nullable=False)
    outcome: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True, index=True
    )
    closed_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    closure_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class OperatorAgentState(Base):
    """Single server-owned operator presence and channel-capacity authority."""

    __tablename__ = "operator_agent_states"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_operator_agent_states_user"),
        CheckConstraint(
            "status IN ('offline', 'online', 'paused')",
            name="ck_operator_agent_states_status",
        ),
        CheckConstraint(
            "max_concurrent_conversations BETWEEN 1 AND 20",
            name="ck_operator_agent_states_capacity",
        ),
        CheckConstraint(
            "max_concurrent_voice_calls BETWEEN 1 AND 5",
            name="ck_operator_agent_states_voice_capacity",
        ),
        CheckConstraint(
            "voice_wrap_up_seconds BETWEEN 0 AND 900",
            name="ck_operator_agent_states_voice_wrap_up",
        ),
        Index(
            "ix_operator_agent_states_status_heartbeat",
            "status",
            "last_heartbeat_at",
        ),
        Index(
            "ix_operator_agent_states_voice_eligibility",
            "voice_enabled",
            "status",
            "last_heartbeat_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="offline", index=True
    )
    max_concurrent_conversations: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3
    )
    voice_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )
    max_concurrent_voice_calls: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )
    voice_wrap_up_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30
    )
    last_heartbeat_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True, index=True
    )
    status_changed_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
