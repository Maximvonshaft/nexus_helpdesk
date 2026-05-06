from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Index, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .utils.time import utc_now

UTCDateTime = DateTime(timezone=True)
ACTIVE_TASK_SQL = "status NOT IN ('resolved', 'dropped', 'replayed', 'replay_failed', 'cancelled')"


class OperatorTask(Base):
    """Operator queue task projected from WebChat handoff or OpenClaw unresolved work.

    This is a soft-reference projection table: source rows are validated and
    closed by service code rather than enforced with hard foreign keys in this
    stacked PR. ``payload_json`` is internal-only, redacted admin context and
    must not be exposed through public WebChat APIs.
    """

    __tablename__ = "operator_tasks"
    __table_args__ = (
        Index("ix_operator_tasks_status_priority_created", "status", "priority", "created_at"),
        Index("ix_operator_tasks_ticket_id", "ticket_id"),
        Index("ix_operator_tasks_webchat_conversation_id", "webchat_conversation_id"),
        Index("ix_operator_tasks_unresolved_event_id", "unresolved_event_id"),
        Index("ix_operator_tasks_source_task", "source_type", "task_type"),
        Index("ix_operator_tasks_assignee_status", "assignee_id", "status"),
        Index(
            "uq_operator_tasks_active_openclaw_unresolved",
            "unresolved_event_id",
            unique=True,
            postgresql_where=text(f"unresolved_event_id IS NOT NULL AND {ACTIVE_TASK_SQL}"),
            sqlite_where=text(f"unresolved_event_id IS NOT NULL AND {ACTIVE_TASK_SQL}"),
        ),
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
            "task_type",
            "source_id",
            unique=True,
            postgresql_where=text(f"source_id IS NOT NULL AND {ACTIVE_TASK_SQL}"),
            sqlite_where=text(f"source_id IS NOT NULL AND {ACTIVE_TASK_SQL}"),
        ),
        {
            "comment": (
                "Soft-reference projection table for WebChat handoff and OpenClaw unresolved work. "
                "Source existence is enforced by service code rather than hard foreign keys in this stacked PR."
            )
        },
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_id: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    ticket_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    webchat_conversation_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    unresolved_event_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    task_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending", server_default=text("'pending'"))
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100, server_default=text("100"))
    assignee_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reason_code: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    payload_json: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Internal-only redacted operator context. Never expose through public WebChat APIs.",
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utc_now, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
