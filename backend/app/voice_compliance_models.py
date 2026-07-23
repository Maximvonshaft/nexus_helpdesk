from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from . import voice_models as _voice_models  # noqa: F401 - register FK target
from .db import Base
from .utils.time import utc_now


class VoiceComplianceEvidence(Base):
    """Immutable capability-level evidence for voice recording and transcript persistence.

    This is a projection of completed notice/consent outcomes. It does not own a
    pending confirmation workflow and must not duplicate AgentToolConfirmation.
    """

    __tablename__ = "voice_compliance_evidence"
    __table_args__ = (
        UniqueConstraint(
            "public_id",
            name="uq_voice_compliance_evidence_public_id",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_voice_compliance_evidence_idempotency",
        ),
        CheckConstraint(
            "capability IN ('recording', 'transcript_persistence')",
            name="ck_voice_compliance_evidence_capability",
        ),
        CheckConstraint(
            "policy IN ('disabled', 'notice', 'explicit_consent')",
            name="ck_voice_compliance_evidence_policy",
        ),
        CheckConstraint(
            "source IN ('browser', 'sip_controller', 'migration')",
            name="ck_voice_compliance_evidence_source",
        ),
        CheckConstraint(
            "decision IN ('notice_delivered', 'accepted', 'declined', 'timeout')",
            name="ck_voice_compliance_evidence_decision",
        ),
        Index(
            "ix_voice_compliance_session_capability_time",
            "voice_session_id",
            "capability",
            "evidence_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(64))
    voice_session_id: Mapped[int] = mapped_column(
        ForeignKey("webchat_voice_sessions.id", ondelete="CASCADE"),
        index=True,
    )
    capability: Mapped[str] = mapped_column(String(32), index=True)
    policy: Mapped[str] = mapped_column(String(32), index=True)
    policy_version: Mapped[str] = mapped_column(String(80), index=True)
    prompt_sha256: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    participant_identity_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    decision: Mapped[str] = mapped_column(String(32), index=True)
    confirmation_public_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(180))
    evidence_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
