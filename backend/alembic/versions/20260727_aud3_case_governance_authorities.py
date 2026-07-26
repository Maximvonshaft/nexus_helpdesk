"""Add versioned SLA, structured outcomes and data lifecycle authorities.

Revision ID: 20260727_aud3
Revises: 20260727_aud2
Create Date: 2026-07-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260727_aud3"
down_revision = "20260727_aud2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sla_policy_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("policy_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.Column("is_global_template", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("market_id", sa.Integer(), nullable=True),
        sa.Column("channel_key", sa.String(length=40), nullable=True),
        sa.Column("scenario_key", sa.String(length=160), nullable=True),
        sa.Column("customer_tier", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="draft"),
        sa.Column("timezone_name", sa.String(length=80), nullable=False, server_default="UTC"),
        sa.Column("weekly_schedule_json", sa.JSON(), nullable=False),
        sa.Column("holidays_json", sa.JSON(), nullable=False),
        sa.Column("first_response_minutes", sa.Integer(), nullable=False),
        sa.Column("resolution_minutes", sa.Integer(), nullable=False),
        sa.Column("action_minutes", sa.Integer(), nullable=True),
        sa.Column("notification_minutes", sa.Integer(), nullable=True),
        sa.Column("risk_window_minutes", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("pause_reasons_json", sa.JSON(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["policy_id"], ["sla_policies.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("policy_id", "version", name="uq_sla_policy_revision_version"),
        sa.CheckConstraint("version > 0", name="ck_sla_revision_version_positive"),
        sa.CheckConstraint("status IN ('draft','approved','retired')", name="ck_sla_revision_status"),
        sa.CheckConstraint("NOT is_global_template OR tenant_id IS NULL", name="ck_sla_revision_global_has_no_tenant"),
        sa.CheckConstraint("is_global_template OR tenant_id IS NOT NULL", name="ck_sla_revision_scoped_has_tenant"),
        sa.CheckConstraint("first_response_minutes > 0 AND resolution_minutes > 0", name="ck_sla_revision_primary_targets_positive"),
        sa.CheckConstraint("action_minutes IS NULL OR action_minutes > 0", name="ck_sla_revision_action_target_positive"),
        sa.CheckConstraint("notification_minutes IS NULL OR notification_minutes > 0", name="ck_sla_revision_notification_target_positive"),
        sa.CheckConstraint("risk_window_minutes >= 0", name="ck_sla_revision_risk_window_nonnegative"),
        sa.CheckConstraint("effective_to IS NULL OR effective_to > effective_from", name="ck_sla_revision_effective_window"),
    )
    for name, columns in (
        ("ix_sla_policy_revisions_policy_id", ["policy_id"]),
        ("ix_sla_policy_revisions_tenant_id", ["tenant_id"]),
        ("ix_sla_policy_revisions_market_id", ["market_id"]),
        ("ix_sla_policy_revisions_channel_key", ["channel_key"]),
        ("ix_sla_policy_revisions_scenario_key", ["scenario_key"]),
        ("ix_sla_policy_revisions_customer_tier", ["customer_tier"]),
        ("ix_sla_policy_revisions_status", ["status"]),
        ("ix_sla_policy_revisions_effective_from", ["effective_from"]),
        ("ix_sla_policy_revisions_effective_to", ["effective_to"]),
        (
            "ix_sla_revision_scope_effective",
            ["tenant_id", "market_id", "channel_key", "scenario_key", "customer_tier", "status", "effective_from"],
        ),
    ):
        op.create_index(name, "sla_policy_revisions", columns, unique=False)

    op.create_table(
        "ticket_sla_assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("policy_revision_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assigned_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["policy_revision_id"], ["sla_policy_revisions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assigned_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("ticket_id", name="uq_ticket_sla_assignment_ticket"),
    )
    op.create_index("ix_ticket_sla_assignments_ticket_id", "ticket_sla_assignments", ["ticket_id"], unique=False)
    op.create_index("ix_ticket_sla_assignment_revision", "ticket_sla_assignments", ["policy_revision_id"], unique=False)

    op.create_table(
        "ticket_sla_pause_intervals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("reason_code", sa.String(length=120), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_by", sa.Integer(), nullable=True),
        sa.Column("ended_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["started_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["ended_by"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="ck_ticket_sla_pause_interval_order"),
    )
    op.create_index("ix_ticket_sla_pause_intervals_ticket_id", "ticket_sla_pause_intervals", ["ticket_id"], unique=False)
    op.create_index("ix_ticket_sla_pause_intervals_reason_code", "ticket_sla_pause_intervals", ["reason_code"], unique=False)
    op.create_index("ix_ticket_sla_pause_intervals_started_at", "ticket_sla_pause_intervals", ["started_at"], unique=False)
    op.create_index("ix_ticket_sla_pause_intervals_ended_at", "ticket_sla_pause_intervals", ["ended_at"], unique=False)
    op.create_index("ix_ticket_sla_pause_history", "ticket_sla_pause_intervals", ["ticket_id", "started_at"], unique=False)
    op.create_index(
        "uq_ticket_sla_pause_open",
        "ticket_sla_pause_intervals",
        ["ticket_id"],
        unique=True,
        sqlite_where=sa.text("ended_at IS NULL"),
        postgresql_where=sa.text("ended_at IS NULL"),
    )

    op.create_table(
        "case_outcome_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("record_type", sa.String(length=40), nullable=False),
        sa.Column("state", sa.String(length=40), nullable=False),
        sa.Column("idempotency_key", sa.String(length=180), nullable=False),
        sa.Column("parent_record_id", sa.Integer(), nullable=True),
        sa.Column("source_kind", sa.String(length=80), nullable=True),
        sa.Column("source_id", sa.String(length=180), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_record_id"], ["case_outcome_records.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("ticket_id", "sequence", name="uq_case_outcome_ticket_sequence"),
        sa.UniqueConstraint("ticket_id", "idempotency_key", name="uq_case_outcome_ticket_idempotency"),
        sa.CheckConstraint("sequence > 0", name="ck_case_outcome_sequence_positive"),
        sa.CheckConstraint("record_type IN ('action_intent','execution_attempt','provider_receipt','operational_outcome','customer_notification','closure_assessment')", name="ck_case_outcome_record_type"),
        sa.CheckConstraint("state IN ('requested','accepted','processing','succeeded','failed','waived','delivered','confirmed','repair_required','blocked','eligible','closed','reopened')", name="ck_case_outcome_state"),
    )
    for name, columns in (
        ("ix_case_outcome_records_ticket_id", ["ticket_id"]),
        ("ix_case_outcome_records_record_type", ["record_type"]),
        ("ix_case_outcome_records_state", ["state"]),
        ("ix_case_outcome_records_parent_record_id", ["parent_record_id"]),
        ("ix_case_outcome_records_source_kind", ["source_kind"]),
        ("ix_case_outcome_records_source_id", ["source_id"]),
        ("ix_case_outcome_records_occurred_at", ["occurred_at"]),
        ("ix_case_outcome_records_created_at", ["created_at"]),
        ("ix_case_outcome_ticket_type_created", ["ticket_id", "record_type", "created_at"]),
        ("ix_case_outcome_source", ["source_kind", "source_id"]),
    ):
        op.create_index(name, "case_outcome_records", columns, unique=False)

    op.create_table(
        "retention_policy_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.Column("is_global_template", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("resource_type", sa.String(length=80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=False),
        sa.Column("legal_basis", sa.String(length=160), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False, server_default="anonymize"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="draft"),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("tenant_id", "resource_type", "version", name="uq_retention_policy_scope_version"),
        sa.CheckConstraint("version > 0", name="ck_retention_version_positive"),
        sa.CheckConstraint("retention_days >= 0", name="ck_retention_days_nonnegative"),
        sa.CheckConstraint("status IN ('draft','approved','retired')", name="ck_retention_status"),
        sa.CheckConstraint("NOT is_global_template OR tenant_id IS NULL", name="ck_retention_global_has_no_tenant"),
        sa.CheckConstraint("is_global_template OR tenant_id IS NOT NULL", name="ck_retention_scoped_has_tenant"),
        sa.CheckConstraint("effective_to IS NULL OR effective_to > effective_from", name="ck_retention_effective_window"),
    )
    op.create_index("ix_retention_policy_versions_tenant_id", "retention_policy_versions", ["tenant_id"], unique=False)
    op.create_index("ix_retention_policy_versions_resource_type", "retention_policy_versions", ["resource_type"], unique=False)
    op.create_index("ix_retention_policy_versions_effective_from", "retention_policy_versions", ["effective_from"], unique=False)
    op.create_index("ix_retention_policy_versions_effective_to", "retention_policy_versions", ["effective_to"], unique=False)
    op.create_index("ix_retention_policy_effective", "retention_policy_versions", ["tenant_id", "resource_type", "status", "effective_from"], unique=False)

    op.create_table(
        "legal_hold_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("ticket_id", sa.Integer(), nullable=True),
        sa.Column("reason_code", sa.String(length=120), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="active"),
        sa.Column("placed_by", sa.Integer(), nullable=True),
        sa.Column("released_by", sa.Integer(), nullable=True),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["placed_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["released_by"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint("customer_id IS NOT NULL OR ticket_id IS NOT NULL", name="ck_legal_hold_has_subject"),
        sa.CheckConstraint("status IN ('active','released')", name="ck_legal_hold_status"),
        sa.CheckConstraint("released_at IS NULL OR released_at >= placed_at", name="ck_legal_hold_release_order"),
    )
    op.create_index("ix_legal_hold_records_tenant_id", "legal_hold_records", ["tenant_id"], unique=False)
    op.create_index("ix_legal_hold_records_customer_id", "legal_hold_records", ["customer_id"], unique=False)
    op.create_index("ix_legal_hold_records_ticket_id", "legal_hold_records", ["ticket_id"], unique=False)
    op.create_index("ix_legal_hold_records_reason_code", "legal_hold_records", ["reason_code"], unique=False)
    op.create_index("ix_legal_hold_records_status", "legal_hold_records", ["status"], unique=False)
    op.create_index("ix_legal_hold_tenant_status", "legal_hold_records", ["tenant_id", "status"], unique=False)

    op.create_table(
        "data_subject_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("request_key", sa.String(length=160), nullable=False),
        sa.Column("request_type", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="received"),
        sa.Column("identity_evidence_hash", sa.String(length=64), nullable=True),
        sa.Column("scope_json", sa.JSON(), nullable=False),
        sa.Column("result_manifest_json", sa.JSON(), nullable=True),
        sa.Column("result_sha256", sa.String(length=64), nullable=True),
        sa.Column("blocked_reason", sa.String(length=160), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("tenant_id", "request_key", name="uq_dsar_tenant_request_key"),
        sa.CheckConstraint("request_type IN ('access','export','delete','restrict','correct')", name="ck_dsar_request_type"),
        sa.CheckConstraint("status IN ('received','identity_pending','qualified','processing','blocked_legal_hold','completed','rejected','cancelled')", name="ck_dsar_status"),
        sa.CheckConstraint("completed_at IS NULL OR completed_at >= received_at", name="ck_dsar_completed_order"),
    )
    op.create_index("ix_data_subject_requests_tenant_id", "data_subject_requests", ["tenant_id"], unique=False)
    op.create_index("ix_data_subject_requests_customer_id", "data_subject_requests", ["customer_id"], unique=False)
    op.create_index("ix_data_subject_requests_request_type", "data_subject_requests", ["request_type"], unique=False)
    op.create_index("ix_data_subject_requests_status", "data_subject_requests", ["status"], unique=False)
    op.create_index("ix_data_subject_requests_due_at", "data_subject_requests", ["due_at"], unique=False)
    op.create_index("ix_dsar_tenant_status_due", "data_subject_requests", ["tenant_id", "status", "due_at"], unique=False)

    op.create_table(
        "data_lifecycle_executions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("policy_version_id", sa.Integer(), nullable=False),
        sa.Column("execution_key", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="planned"),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scanned_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("affected_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("held_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("receipt_json", sa.JSON(), nullable=False),
        sa.Column("receipt_sha256", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["policy_version_id"], ["retention_policy_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("tenant_id", "execution_key", name="uq_data_lifecycle_tenant_execution"),
        sa.CheckConstraint("status IN ('planned','dry_run','applied','failed','cancelled')", name="ck_data_lifecycle_status"),
        sa.CheckConstraint("scanned_count >= 0 AND affected_count >= 0 AND held_count >= 0", name="ck_data_lifecycle_counts_nonnegative"),
    )
    op.create_index("ix_data_lifecycle_executions_tenant_id", "data_lifecycle_executions", ["tenant_id"], unique=False)
    op.create_index("ix_data_lifecycle_executions_policy_version_id", "data_lifecycle_executions", ["policy_version_id"], unique=False)
    op.create_index("ix_data_lifecycle_executions_status", "data_lifecycle_executions", ["status"], unique=False)
    op.create_index("ix_data_lifecycle_executions_created_at", "data_lifecycle_executions", ["created_at"], unique=False)
    op.create_index("ix_data_lifecycle_tenant_created", "data_lifecycle_executions", ["tenant_id", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_table("data_lifecycle_executions")
    op.drop_table("data_subject_requests")
    op.drop_table("legal_hold_records")
    op.drop_table("retention_policy_versions")
    op.drop_table("case_outcome_records")
    op.drop_table("ticket_sla_pause_intervals")
    op.drop_table("ticket_sla_assignments")
    op.drop_table("sla_policy_revisions")
