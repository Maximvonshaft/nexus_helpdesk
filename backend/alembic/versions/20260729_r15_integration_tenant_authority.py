"""Add explicit Integration principal and audit-receipt scope envelopes.

Revision ID: 20260729_r15_integration_scope
Revises: 20260729_wa5_signup_checkpoint
Create Date: 2026-07-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260729_r15_integration_scope"
down_revision = "20260729_wa5_signup_checkpoint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "integration_client_scopes",
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("scope_type", sa.String(length=24), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.Column(
            "assignment_source",
            sa.String(length=80),
            nullable=False,
            server_default="explicit_admin",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "scope_type IN ('tenant','platform')",
            name="ck_integration_client_scope_type",
        ),
        sa.CheckConstraint(
            "(scope_type = 'tenant' AND tenant_id IS NOT NULL) OR "
            "(scope_type = 'platform' AND tenant_id IS NULL)",
            name="ck_integration_client_scope_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["integration_clients.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("client_id"),
    )
    op.create_index(
        "ix_integration_client_scopes_scope_type",
        "integration_client_scopes",
        ["scope_type"],
        unique=False,
    )
    op.create_index(
        "ix_integration_client_scopes_tenant_id",
        "integration_client_scopes",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_integration_client_scopes_tenant",
        "integration_client_scopes",
        ["tenant_id", "client_id"],
        unique=False,
    )

    op.create_table(
        "integration_request_log_envelopes",
        sa.Column("log_id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=True),
        sa.Column(
            "principal_scope_type",
            sa.String(length=24),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.Column("purpose", sa.String(length=80), nullable=False),
        sa.Column("response_schema", sa.String(length=80), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "principal_scope_type IN ('tenant','platform')",
            name="ck_integration_log_envelope_scope_type",
        ),
        sa.CheckConstraint(
            "principal_scope_type <> 'tenant' OR tenant_id IS NOT NULL",
            name="ck_integration_log_envelope_tenant_required",
        ),
        sa.CheckConstraint(
            "length(trim(purpose)) > 0",
            name="ck_integration_log_envelope_purpose_nonempty",
        ),
        sa.ForeignKeyConstraint(
            ["log_id"],
            ["integration_request_logs.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["integration_clients.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("log_id"),
    )
    for name, columns in (
        (
            "ix_integration_request_log_envelopes_client_id",
            ["client_id"],
        ),
        (
            "ix_integration_request_log_envelopes_principal_scope_type",
            ["principal_scope_type"],
        ),
        (
            "ix_integration_request_log_envelopes_tenant_id",
            ["tenant_id"],
        ),
        (
            "ix_integration_request_log_envelopes_purpose",
            ["purpose"],
        ),
        (
            "ix_integration_request_log_envelopes_expires_at",
            ["expires_at"],
        ),
        (
            "ix_integration_request_log_envelopes_created_at",
            ["created_at"],
        ),
        (
            "ix_integration_log_envelope_tenant_expiry",
            ["tenant_id", "expires_at", "log_id"],
        ),
        (
            "ix_integration_log_envelope_purpose_expiry",
            ["purpose", "expires_at", "log_id"],
        ),
    ):
        op.create_index(
            name,
            "integration_request_log_envelopes",
            columns,
            unique=False,
        )

    # Existing credentials have no factually defensible Tenant or Platform
    # authority. Disable them fail closed; administrators must create a new
    # explicitly scoped credential or perform a separately reviewed migration.
    op.execute(sa.text("UPDATE integration_clients SET is_active = false"))

    # Historical response_json can contain Customer PII. It has no subject or
    # Tenant authority, so the only safe automatic migration is removal.
    op.execute(
        sa.text(
            "UPDATE integration_request_logs "
            "SET response_json = NULL, error_code = COALESCE(error_code, "
            "'r15_historical_payload_purged')"
        )
    )


def downgrade() -> None:
    # Credential activation is not restored automatically. A pre-R15 runtime
    # must still receive an explicit operator decision rather than silently
    # reviving unscoped credentials.
    op.drop_table("integration_request_log_envelopes")
    op.drop_table("integration_client_scopes")
