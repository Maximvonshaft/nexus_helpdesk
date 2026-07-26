from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .utils.time import utc_now

UTCDateTime = DateTime(timezone=True)


class CaseEvidenceRecord(Base):
    """Append-only authoritative evidence for one Ticket-as-Case.

    Evidence is intentionally separate from action/outcome records. It stores a
    bounded source identity and content-safe metadata, never raw Provider or
    customer payloads.
    """

    __tablename__ = "case_evidence_records"
    __table_args__ = (
        UniqueConstraint(
            "ticket_id",
            "evidence_key",
            "source_kind",
            "source_ref",
            "source_revision",
            name="uq_case_evidence_source_revision",
        ),
        CheckConstraint(
            "evidence_kind IN ('fact','customer_input')",
            name="ck_case_evidence_kind",
        ),
        CheckConstraint(
            "state IN ('verified','completed','waived','failed')",
            name="ck_case_evidence_state",
        ),
        CheckConstraint(
            "length(trim(evidence_key)) > 0",
            name="ck_case_evidence_key_nonempty",
        ),
        CheckConstraint(
            "length(trim(source_ref)) > 0 AND length(trim(source_revision)) > 0",
            name="ck_case_evidence_source_identity",
        ),
        Index(
            "ix_case_evidence_ticket_kind_created",
            "ticket_id",
            "evidence_kind",
            "created_at",
        ),
        Index(
            "ix_case_evidence_source",
            "source_kind",
            "source_ref",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    evidence_kind: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        index=True,
    )
    evidence_key: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
        index=True,
    )
    state: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        index=True,
    )
    source_kind: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
        index=True,
    )
    source_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    source_revision: Mapped[str] = mapped_column(String(160), nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    safe_metadata_json: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    observed_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        index=True,
    )
    recorded_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utc_now,
        index=True,
    )
