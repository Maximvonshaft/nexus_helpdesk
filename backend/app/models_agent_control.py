from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .utils.time import utc_now

UTCDateTime = DateTime(timezone=True)


class AgentDefinition(Base):
    """Tenant-scoped editable Agent composition.

    Definitions are the only mutable authoring object. Runtime never executes a
    draft definition directly; it executes an immutable AgentRelease selected by
    AgentDeployment.
    """

    __tablename__ = "agent_definitions"
    __table_args__ = (
        UniqueConstraint("tenant_key", "definition_key", name="uq_agent_definition_tenant_key"),
        CheckConstraint("length(trim(tenant_key)) > 0", name="ck_agent_definition_tenant_nonempty"),
        CheckConstraint("length(trim(definition_key)) > 0", name="ck_agent_definition_key_nonempty"),
        Index("ix_agent_definitions_tenant_active", "tenant_key", "is_active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_key: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    definition_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    purpose: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    owner_team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"), nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    draft_manifest_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    updated_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now, onupdate=utc_now, index=True
    )


class AgentRelease(Base):
    """Immutable, validated Agent artifact consumed by deployments."""

    __tablename__ = "agent_releases"
    __table_args__ = (
        UniqueConstraint("definition_id", "version", name="uq_agent_release_definition_version"),
        CheckConstraint(
            "status IN ('approved', 'canary', 'active', 'retired')",
            name="ck_agent_release_status",
        ),
        Index("ix_agent_releases_definition_status", "definition_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    definition_id: Mapped[int] = mapped_column(
        ForeignKey("agent_definitions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="approved", index=True)
    manifest_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    validation_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    approved_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utc_now, index=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)


class AgentDeployment(Base):
    """Atomic scope pointer selecting an immutable AgentRelease."""

    __tablename__ = "agent_deployments"
    __table_args__ = (
        UniqueConstraint(
            "tenant_key", "environment", "scope_key", name="uq_agent_deployment_scope"
        ),
        CheckConstraint("canary_percent >= 0 AND canary_percent <= 100", name="ck_agent_canary_percent"),
        CheckConstraint("environment IN ('test', 'staging', 'production')", name="ck_agent_environment"),
        Index("ix_agent_deployments_lookup", "tenant_key", "environment", "is_active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_key: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    environment: Mapped[str] = mapped_column(String(24), nullable=False, default="production", index=True)
    scope_key: Mapped[str] = mapped_column(String(320), nullable=False)
    market_id: Mapped[Optional[int]] = mapped_column(ForeignKey("markets.id"), nullable=True, index=True)
    channel: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    language: Mapped[Optional[str]] = mapped_column(String(24), nullable=True, index=True)
    case_type: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    active_release_id: Mapped[int] = mapped_column(ForeignKey("agent_releases.id"), nullable=False, index=True)
    canary_release_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("agent_releases.id"), nullable=True, index=True
    )
    canary_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    activated_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    activated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now, onupdate=utc_now, index=True
    )


class AgentRunSnapshot(Base):
    """Immutable evidence of the exact configuration used for one Agent run."""

    __tablename__ = "agent_run_snapshots"
    __table_args__ = (
        UniqueConstraint("request_id", name="uq_agent_run_snapshot_request"),
        Index("ix_agent_run_snapshots_tenant_session", "tenant_key", "session_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    tenant_key: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    deployment_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("agent_deployments.id"), nullable=True, index=True
    )
    release_id: Mapped[Optional[int]] = mapped_column(ForeignKey("agent_releases.id"), nullable=True, index=True)
    snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="deployment")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utc_now, index=True)
