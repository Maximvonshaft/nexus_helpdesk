from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .utils.time import utc_now

UTCDateTime = DateTime(timezone=True)


class TicketScenarioAssignment(Base):
    """Canonical frozen Scenario contract for one Case/Ticket.

    Runtime routing, Tool policy and closure consume this row only. Legacy
    classification fields may be used once by the migration command to create
    this record, but never override it implicitly afterwards.
    """

    __tablename__ = "ticket_scenario_assignments"
    __table_args__ = (
        Index(
            "ix_ticket_scenario_assignment_tenant_key",
            "tenant_id",
            "scenario_key",
            "assigned_at",
        ),
        Index(
            "ix_ticket_scenario_assignment_catalog",
            "catalog_version",
            "catalog_sha256",
        ),
    )

    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tenant_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    scenario_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    assignment_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    catalog_version: Mapped[str] = mapped_column(String(80), nullable=False)
    catalog_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    definition_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    definition_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    assignment_source: Mapped[str] = mapped_column(String(40), nullable=False)
    assignment_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    assigned_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    assigned_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utc_now,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
