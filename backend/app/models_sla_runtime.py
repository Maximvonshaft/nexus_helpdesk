from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from .db import Base
from .utils.time import utc_now


class UTCDateTime(TypeDecorator):
    """Persist timezone-aware instants and restore UTC on dialects that strip it."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):  # noqa: ANN001
        del dialect
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):  # noqa: ANN001
        del dialect
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class TicketSLATarget(Base):
    """Current query projection derived from immutable SLA assignment.

    The policy contract is owned by ``TicketSLAAssignment`` and its immutable
    snapshot. This table stores calculated due/risk instants so queue and Control
    Tower queries do not rely on a global risk-window constant or dialect-specific
    dynamic interval arithmetic. Ticket due fields remain bounded API caches.
    """

    __tablename__ = "ticket_sla_targets"
    __table_args__ = (
        UniqueConstraint("ticket_id", name="uq_ticket_sla_target_ticket"),
        Index(
            "ix_ticket_sla_target_first_risk",
            "first_response_risk_at",
            "ticket_id",
        ),
        Index(
            "ix_ticket_sla_target_resolution_risk",
            "resolution_risk_at",
            "ticket_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    assignment_id: Mapped[int] = mapped_column(
        ForeignKey("ticket_sla_assignments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    first_response_due_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        index=True,
    )
    resolution_due_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        index=True,
    )
    first_response_risk_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        index=True,
    )
    resolution_risk_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        index=True,
    )
    paused_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    calculated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utc_now,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
    source_revision: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
