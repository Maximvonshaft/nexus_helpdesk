from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .utils.time import utc_now

UTCDateTime = DateTime(timezone=True)
ACTIVE_TASK_SQL = "status NOT IN ('resolved', 'dropped', 'replayed', 'replay_failed', 'cancelled')"


class OperatorTask(Base):
    """Durable operator task projected from canonical support workflows."""

    __tablename__ = "operator_tasks"
    __table_args__ = (
        Index("ix_operator_tasks_status_priority_created", "status", "priority", "created_at"),
        Index("ix_operator_tasks_source_status", "source_type", "status"),
        Index("ix_operator_tasks_task_status", "task_type", "status"),
        Index("ix_operator_tasks_ticket_id", "ticket_id"),
        Index("ix_operator_tasks_webchat_conversation_id", "webchat_conversation_id"),
        Index("ix_operator_tasks_assignee_id", "assignee_id"),
        Index("ix_operator_tasks_reason_code", "reason_code"),
        Index(
            "uq_operator_tasks_active_webchat_handoff",
            "webchat_conversation_id",
            "task_type",
            unique=True,
            postgresql_where=text(f"webchat_conversation_id IS NOT NULL AND {ACTIVE_TASK_SQL}"),
            sqlite_where=text(f"webchat_conversation_id IS NOT NULL AND {ACTIVE_TASK_SQL}"),
        ),
        Index(
            "uq_operator_tasks_active_source",
            "source_type",
            "source_id",
            "task_type",
            unique=True,
            postgresql_where=text(f"source_id IS NOT NULL AND {ACTIVE_TASK_SQL}"),
            sqlite_where=text(f"source_id IS NOT NULL AND {ACTIVE_TASK_SQL}"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_type: Mapped[str] = mapped_column(String(40))
    source_id: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    ticket_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    webchat_conversation_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    task_type: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(40), default="pending")
    priority: Mapped[int] = mapped_column(Integer, default=100)
    assignee_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reason_code: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)


class OperatorQueueScopeGrant(Base):
    """Exact server-owned visibility scope for the live unified queue.

    This table is authorization policy only. It intentionally stores no queue
    item, customer payload or mutable workflow state.
    """

    __tablename__ = "operator_queue_scope_grants"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "tenant_key",
            "country_code",
            "channel_key",
            name="uq_operator_queue_scope_grant",
        ),
        Index("ix_operator_queue_scope_grants_user_enabled", "user_id", "enabled"),
        Index(
            "ix_operator_queue_scope_grants_scope",
            "tenant_key",
            "country_code",
            "channel_key",
            "enabled",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    tenant_key: Mapped[str] = mapped_column(String(80), nullable=False)
    country_code: Mapped[str] = mapped_column(String(16), nullable=False)
    channel_key: Mapped[str] = mapped_column(String(40), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    granted_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
