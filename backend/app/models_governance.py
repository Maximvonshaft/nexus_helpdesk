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
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .utils.time import utc_now


UTCDateTime = DateTime(timezone=True)


class CountryCatalog(Base):
    """Platform-owned ISO country reference data."""

    __tablename__ = "country_catalog"
    __table_args__ = (
        UniqueConstraint("iso_alpha3", name="uq_country_catalog_alpha3"),
        UniqueConstraint("iso_numeric", name="uq_country_catalog_numeric"),
        Index("ix_country_catalog_alpha3", "iso_alpha3"),
        Index("ix_country_catalog_name", "canonical_name"),
        Index("ix_country_catalog_currency", "default_currency"),
        Index("ix_country_catalog_available", "is_available"),
    )

    iso_alpha2: Mapped[str] = mapped_column(String(2), primary_key=True)
    iso_alpha3: Mapped[str] = mapped_column(String(3))
    iso_numeric: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    canonical_name: Mapped[str] = mapped_column(String(160))
    calling_code: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    default_currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now, onupdate=utc_now
    )


class MarketGovernanceProfile(Base):
    """Lifecycle and operator-facing metadata for the canonical Market row."""

    __tablename__ = "market_governance_profiles"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'active', 'paused', 'retiring', 'retired')",
            name="ck_market_governance_status",
        ),
        CheckConstraint("version > 0", name="ck_market_governance_version_positive"),
        Index("ix_market_governance_status", "status"),
        Index("ix_market_governance_owner_team_id", "owner_team_id"),
        Index("ix_market_governance_retired_by", "retired_by"),
        Index("ix_market_governance_retired_at", "retired_at"),
        Index("ix_market_governance_created_by", "created_by"),
        Index("ix_market_governance_updated_by", "updated_by"),
    )

    market_id: Mapped[int] = mapped_column(
        ForeignKey("markets.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    default_currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    owner_team_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), nullable=True
    )
    data_region: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    retired_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    retired_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True
    )
    created_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now, onupdate=utc_now
    )


class MarketCountry(Base):
    __tablename__ = "market_countries"
    __table_args__ = (
        UniqueConstraint("market_id", "country_code", name="uq_market_country"),
        Index("ix_market_country_primary", "market_id", "is_primary"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    market_id: Mapped[int] = mapped_column(
        ForeignKey("markets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    country_code: Mapped[str] = mapped_column(
        ForeignKey("country_catalog.iso_alpha2", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utc_now)


class MarketLanguage(Base):
    __tablename__ = "market_languages"
    __table_args__ = (
        UniqueConstraint("market_id", "language_code", name="uq_market_language"),
        Index("ix_market_language_primary", "market_id", "is_primary"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    market_id: Mapped[int] = mapped_column(
        ForeignKey("markets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    language_code: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utc_now)


class RoleTemplate(Base):
    """Tenant-owned reusable capability projection.

    Templates compile into the existing User.role plus UserCapabilityOverride
    authority. Runtime authorization never reads this table directly.
    """

    __tablename__ = "role_templates"
    __table_args__ = (
        CheckConstraint(
            "base_role IN ('admin', 'manager', 'lead', 'agent', 'auditor')",
            name="ck_role_template_base_role",
        ),
        CheckConstraint(
            "risk_level IN ('standard', 'sensitive', 'administrator')",
            name="ck_role_template_risk",
        ),
        CheckConstraint(
            "published_version >= 0", name="ck_role_template_version_nonnegative"
        ),
        Index(
            "uq_role_template_global_key",
            "role_key",
            unique=True,
            sqlite_where=text("tenant_id IS NULL"),
            postgresql_where=text("tenant_id IS NULL"),
        ),
        Index(
            "uq_role_template_tenant_key",
            "tenant_id",
            "role_key",
            unique=True,
            sqlite_where=text("tenant_id IS NOT NULL"),
            postgresql_where=text("tenant_id IS NOT NULL"),
        ),
        Index("ix_role_template_tenant_active", "tenant_id", "is_active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    role_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    base_role: Mapped[str] = mapped_column(String(32), nullable=False)
    risk_level: Mapped[str] = mapped_column(
        String(24), nullable=False, default="standard", index=True
    )
    is_system_protected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    draft_capabilities_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    published_capabilities_json: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    published_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    published_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True, index=True
    )
    created_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    updated_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    published_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now, onupdate=utc_now
    )


class RoleTemplateVersion(Base):
    __tablename__ = "role_template_versions"
    __table_args__ = (
        UniqueConstraint("template_id", "version", name="uq_role_template_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[int] = mapped_column(
        ForeignKey("role_templates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    published_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    published_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now, index=True
    )


class RoleTemplateAssignment(Base):
    __tablename__ = "role_template_assignments"
    __table_args__ = (
        CheckConstraint(
            "template_version > 0", name="ck_role_template_assignment_version_positive"
        ),
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    template_id: Mapped[int] = mapped_column(
        ForeignKey("role_templates.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    template_version: Mapped[int] = mapped_column(Integer, nullable=False)
    assigned_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    assigned_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now, index=True
    )


class KnowledgeImportBatch(Base):
    __tablename__ = "knowledge_import_batches"
    __table_args__ = (
        CheckConstraint(
            "status IN ('processing', 'ready', 'partial', 'failed', 'cancelled')",
            name="ck_knowledge_import_batch_status",
        ),
        CheckConstraint(
            "total_files >= 0 AND succeeded_files >= 0 AND failed_files >= 0 "
            "AND duplicate_files >= 0",
            name="ck_knowledge_import_batch_counts_nonnegative",
        ),
        CheckConstraint(
            "succeeded_files + failed_files + duplicate_files <= total_files",
            name="ck_knowledge_import_batch_counts_bounded",
        ),
        Index("ix_knowledge_import_batch_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="processing", index=True
    )
    total_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    succeeded_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    market_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("markets.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    channel: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    audience_scope: Mapped[str] = mapped_column(
        String(40), nullable=False, default="customer"
    )
    language: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    created_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now, index=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True, index=True
    )


class KnowledgeImportDocument(Base):
    __tablename__ = "knowledge_import_documents"
    __table_args__ = (
        UniqueConstraint("batch_id", "position", name="uq_knowledge_import_position"),
        CheckConstraint("position > 0", name="ck_knowledge_import_document_position_positive"),
        CheckConstraint(
            "status IN ('draft_created', 'duplicate', 'failed')",
            name="ck_knowledge_import_document_status",
        ),
        Index("ix_knowledge_import_document_hash", "tenant_id", "sha256"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_import_batches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    original_file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    knowledge_item_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("knowledge_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    duplicate_of_document_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("knowledge_import_documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    error_code: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now, index=True
    )


class AgentDeploymentRevision(Base):
    __tablename__ = "agent_deployment_revisions"
    __table_args__ = (
        UniqueConstraint(
            "deployment_id", "revision", name="uq_agent_deployment_revision"
        ),
        CheckConstraint(
            "action IN ('deploy', 'canary_start', 'canary_adjust', 'canary_pause', 'canary_promote', 'rollback')",
            name="ck_agent_deployment_revision_action",
        ),
        CheckConstraint(
            "revision > 0", name="ck_agent_deployment_revision_positive"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    deployment_id: Mapped[int] = mapped_column(
        ForeignKey("agent_deployments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    before_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    after_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now, index=True
    )
