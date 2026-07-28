from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .utils.time import utc_now

UTCDateTime = DateTime(timezone=True)


class CaseScenarioAssignment(Base):
    """Immutable, Case-owned assignment to one versioned business scenario.

    A Ticket may have historical assignments, but exactly one current assignment.
    The complete scenario contract is snapshotted so an in-flight Case cannot
    silently inherit changed rules from a later catalog publication. Assignment
    identity and contract columns are immutable after insert; reclassification
    creates a new row and supersedes the previous row.
    """

    __tablename__ = "case_scenario_assignments"
    __table_args__ = (
        Index(
            "uq_case_scenario_assignments_current_ticket",
            "ticket_id",
            unique=True,
            postgresql_where=text("superseded_at IS NULL"),
            sqlite_where=text("superseded_at IS NULL"),
        ),
        Index(
            "ix_case_scenario_assignments_current_scenario",
            "scenario_key",
            "assigned_at",
            postgresql_where=text("superseded_at IS NULL"),
            sqlite_where=text("superseded_at IS NULL"),
        ),
        Index(
            "ix_case_scenario_assignments_catalog",
            "catalog_version",
            "catalog_sha256",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scenario_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    catalog_version: Mapped[str] = mapped_column(String(120), nullable=False)
    catalog_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    scenario_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    assignment_source: Mapped[str] = mapped_column(String(80), nullable=False)
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
    superseded_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime,
        nullable=True,
        index=True,
    )
    superseded_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("case_scenario_assignments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )


# Register lifecycle invariants whenever this required model family is loaded.
from .services import case_scenario_service as _case_scenario_service  # noqa: E402,F401
