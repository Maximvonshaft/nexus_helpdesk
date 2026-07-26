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


class SLAPolicyRevision(Base):
    """Immutable, scoped SLA contract revision.

    ``SLAPolicy`` remains the stable policy family/priority identity. Runtime
    selection and historical interpretation use this immutable revision.
    """

    __tablename__ = "sla_policy_revisions"
    __table_args__ = (
        UniqueConstraint(
            "policy_id",
            "version",
            name="uq_sla_policy_revision_version",
        ),
        CheckConstraint("version > 0", name="ck_sla_revision_version_positive"),
        CheckConstraint(
            "status IN ('draft','approved','retired')",
            name="ck_sla_revision_status",
        ),
        CheckConstraint(
            "NOT is_global_template OR tenant_id IS NULL",
            name="ck_sla_revision_global_has_no_tenant",
        ),
        CheckConstraint(
            "is_global_template OR tenant_id IS NOT NULL",
            name="ck_sla_revision_scoped_has_tenant",
        ),
        CheckConstraint(
            "first_response_minutes > 0 AND resolution_minutes > 0",
            name="ck_sla_revision_primary_targets_positive",
        ),
        CheckConstraint(
            "action_minutes IS NULL OR action_minutes > 0",
            name="ck_sla_revision_action_target_positive",
        ),
        CheckConstraint(
            "notification_minutes IS NULL OR notification_minutes > 0",
            name="ck_sla_revision_notification_target_positive",
        ),
        CheckConstraint(
            "risk_window_minutes >= 0",
            name="ck_sla_revision_risk_window_nonnegative",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_sla_revision_effective_window",
        ),
        Index(
            "ix_sla_revision_scope_effective",
            "tenant_id",
            "market_id",
            "channel_key",
            "scenario_key",
            "customer_tier",
            "status",
            "effective_from",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    policy_id: Mapped[int] = mapped_column(
        ForeignKey("sla_policies.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    tenant_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    is_global_template: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
    )
    market_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("markets.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    channel_key: Mapped[Optional[str]] = mapped_column(
        String(40),
        nullable=True,
        index=True,
    )
    scenario_key: Mapped[Optional[str]] = mapped_column(
        String(160),
        nullable=True,
        index=True,
    )
    customer_tier: Mapped[Optional[str]] = mapped_column(
        String(80),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="draft",
        index=True,
    )
    timezone_name: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
        default="UTC",
    )
    weekly_schedule_json: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    holidays_json: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    first_response_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    resolution_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    action_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    notification_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    risk_window_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=30,
    )
    pause_reasons_json: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    effective_from: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        index=True,
    )
    effective_to: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime,
        nullable=True,
        index=True,
    )
    approved_by: Mapped[Optional[int]] = mapped_column(
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


class TicketSLAAssignment(Base):
    """Immutable policy snapshot selected for one Ticket-as-Case."""

    __tablename__ = "ticket_sla_assignments"
    __table_args__ = (
        UniqueConstraint("ticket_id", name="uq_ticket_sla_assignment_ticket"),
        Index("ix_ticket_sla_assignment_revision", "policy_revision_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    policy_revision_id: Mapped[int] = mapped_column(
        ForeignKey("sla_policy_revisions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utc_now,
        index=True,
    )
    assigned_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )


class TicketSLAPauseInterval(Base):
    """Append-only SLA pause interval; one open interval per Ticket."""

    __tablename__ = "ticket_sla_pause_intervals"
    __table_args__ = (
        CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name="ck_ticket_sla_pause_interval_order",
        ),
        Index(
            "uq_ticket_sla_pause_open",
            "ticket_id",
            unique=True,
            sqlite_where=text("ended_at IS NULL"),
            postgresql_where=text("ended_at IS NULL"),
        ),
        Index(
            "ix_ticket_sla_pause_history",
            "ticket_id",
            "started_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reason_code: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utc_now,
        index=True,
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime,
        nullable=True,
        index=True,
    )
    started_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    ended_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utc_now,
    )


class CaseOutcomeRecord(Base):
    """Append-only structured business result ledger for Ticket-as-Case."""

    __tablename__ = "case_outcome_records"
    __table_args__ = (
        UniqueConstraint(
            "ticket_id",
            "sequence",
            name="uq_case_outcome_ticket_sequence",
        ),
        UniqueConstraint(
            "ticket_id",
            "idempotency_key",
            name="uq_case_outcome_ticket_idempotency",
        ),
        CheckConstraint("sequence > 0", name="ck_case_outcome_sequence_positive"),
        CheckConstraint(
            "record_type IN ("
            "'action_intent','execution_attempt','provider_receipt',"
            "'operational_outcome','customer_notification','closure_assessment'"
            ")",
            name="ck_case_outcome_record_type",
        ),
        CheckConstraint(
            "state IN ("
            "'requested','accepted','processing','succeeded','failed','waived',"
            "'delivered','confirmed','repair_required','blocked','eligible','closed','reopened'"
            ")",
            name="ck_case_outcome_state",
        ),
        Index(
            "ix_case_outcome_ticket_type_created",
            "ticket_id",
            "record_type",
            "created_at",
        ),
        Index(
            "ix_case_outcome_source",
            "source_kind",
            "source_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    record_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    parent_record_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("case_outcome_records.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_kind: Mapped[Optional[str]] = mapped_column(
        String(80),
        nullable=True,
        index=True,
    )
    source_id: Mapped[Optional[str]] = mapped_column(
        String(180),
        nullable=True,
        index=True,
    )
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utc_now,
        index=True,
    )
    created_by: Mapped[Optional[int]] = mapped_column(
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


class RetentionPolicyVersion(Base):
    """Versioned retention rule, scoped to one Tenant or a global template."""

    __tablename__ = "retention_policy_versions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "resource_type",
            "version",
            name="uq_retention_policy_scope_version",
        ),
        CheckConstraint("version > 0", name="ck_retention_version_positive"),
        CheckConstraint("retention_days >= 0", name="ck_retention_days_nonnegative"),
        CheckConstraint(
            "status IN ('draft','approved','retired')",
            name="ck_retention_status",
        ),
        CheckConstraint(
            "NOT is_global_template OR tenant_id IS NULL",
            name="ck_retention_global_has_no_tenant",
        ),
        CheckConstraint(
            "is_global_template OR tenant_id IS NOT NULL",
            name="ck_retention_scoped_has_tenant",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_retention_effective_window",
        ),
        Index(
            "ix_retention_policy_effective",
            "tenant_id",
            "resource_type",
            "status",
            "effective_from",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    is_global_template: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False)
    legal_basis: Mapped[str] = mapped_column(String(160), nullable=False)
    action: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="anonymize",
    )
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="draft",
        index=True,
    )
    effective_from: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        index=True,
    )
    effective_to: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime,
        nullable=True,
        index=True,
    )
    approved_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utc_now,
    )


class LegalHoldRecord(Base):
    """Audited hold preventing retention/deletion for a subject or Case."""

    __tablename__ = "legal_hold_records"
    __table_args__ = (
        CheckConstraint(
            "customer_id IS NOT NULL OR ticket_id IS NOT NULL",
            name="ck_legal_hold_has_subject",
        ),
        CheckConstraint(
            "status IN ('active','released')",
            name="ck_legal_hold_status",
        ),
        CheckConstraint(
            "released_at IS NULL OR released_at >= placed_at",
            name="ck_legal_hold_release_order",
        ),
        Index(
            "ix_legal_hold_tenant_status",
            "tenant_id",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    ticket_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    reason_code: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="active",
        index=True,
    )
    placed_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    released_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    placed_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utc_now,
        index=True,
    )
    released_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime,
        nullable=True,
        index=True,
    )


class DataSubjectRequest(Base):
    """Durable DSAR workflow bound to Tenant and Customer authority."""

    __tablename__ = "data_subject_requests"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "request_key",
            name="uq_dsar_tenant_request_key",
        ),
        CheckConstraint(
            "request_type IN ('access','export','delete','restrict','correct')",
            name="ck_dsar_request_type",
        ),
        CheckConstraint(
            "status IN ("
            "'received','identity_pending','qualified','processing','blocked_legal_hold',"
            "'completed','rejected','cancelled'"
            ")",
            name="ck_dsar_status",
        ),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= received_at",
            name="ck_dsar_completed_order",
        ),
        Index(
            "ix_dsar_tenant_status_due",
            "tenant_id",
            "status",
            "due_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    request_key: Mapped[str] = mapped_column(String(160), nullable=False)
    request_type: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="received",
        index=True,
    )
    identity_evidence_hash: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
    )
    scope_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    result_manifest_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    result_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    blocked_reason: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utc_now,
        index=True,
    )
    due_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime,
        nullable=True,
        index=True,
    )
    created_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )


class DataLifecycleExecution(Base):
    """Bounded retention execution receipt; never an implicit scheduler."""

    __tablename__ = "data_lifecycle_executions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "execution_key",
            name="uq_data_lifecycle_tenant_execution",
        ),
        CheckConstraint(
            "status IN ('planned','dry_run','applied','failed','cancelled')",
            name="ck_data_lifecycle_status",
        ),
        CheckConstraint(
            "scanned_count >= 0 AND affected_count >= 0 AND held_count >= 0",
            name="ck_data_lifecycle_counts_nonnegative",
        ),
        Index(
            "ix_data_lifecycle_tenant_created",
            "tenant_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    policy_version_id: Mapped[int] = mapped_column(
        ForeignKey("retention_policy_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    execution_key: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="planned",
        index=True,
    )
    cutoff_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    scanned_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    affected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    held_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    receipt_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    receipt_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utc_now,
        index=True,
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime,
        nullable=True,
        index=True,
    )
