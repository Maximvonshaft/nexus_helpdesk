from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
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


class AgentSessionCheckpoint(Base):
    """Release-scoped, expiring operational checkpoint for one Agent session.

    The checkpoint contains only bounded intent/action/Tool outcome metadata. It
    is not customer long-term memory and never stores raw messages, replies,
    prompts, Tool arguments/results, credentials or hidden reasoning.
    """

    __tablename__ = "agent_session_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "tenant_key",
            "session_id",
            "version",
            name="uq_agent_session_checkpoint_version",
        ),
        CheckConstraint("version > 0", name="ck_agent_session_checkpoint_version"),
        CheckConstraint(
            "estimated_tokens >= 0",
            name="ck_agent_session_checkpoint_tokens_nonnegative",
        ),
        Index(
            "ix_agent_session_checkpoints_active",
            "tenant_key",
            "session_id",
            "is_active",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_key: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    release_id: Mapped[int] = mapped_column(
        ForeignKey("agent_releases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_run_id: Mapped[int] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    summary_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    summary_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    estimated_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, index=True
    )
    deactivated_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True, index=True
    )
