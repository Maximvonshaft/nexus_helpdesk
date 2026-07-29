"""Add canonical WhatsApp dual-transport connection state.

Revision ID: 20260727_wa1
Revises: 20260727_r4p0c
Create Date: 2026-07-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260727_wa1"
down_revision = "20260727_r4p0c"
branch_labels = None
depends_on = None


_INDEXES: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    ("ix_whatsapp_connections_tenant_id", ("tenant_id",), False),
    ("ix_whatsapp_connections_channel_account_id", ("channel_account_id",), True),
    ("ix_whatsapp_connections_transport", ("transport",), False),
    ("ix_whatsapp_connections_desired_state", ("desired_state",), False),
    ("ix_whatsapp_connections_observed_state", ("observed_state",), False),
    (
        "ix_whatsapp_connections_authentication_state",
        ("authentication_state",),
        False,
    ),
    ("ix_whatsapp_connections_listener_state", ("listener_state",), False),
    (
        "ix_whatsapp_connections_verification_state",
        ("verification_state",),
        False,
    ),
    ("ix_whatsapp_connections_waba_id", ("waba_id",), False),
    ("ix_whatsapp_connections_phone_number_id", ("phone_number_id",), True),
    ("ix_whatsapp_connections_created_by", ("created_by",), False),
    ("ix_whatsapp_connections_updated_by", ("updated_by",), False),
    ("ix_whatsapp_connections_created_at", ("created_at",), False),
    ("ix_whatsapp_connections_updated_at", ("updated_at",), False),
    (
        "ix_whatsapp_connection_tenant_transport_state",
        ("tenant_id", "transport", "desired_state", "observed_state"),
        False,
    ),
    (
        "ix_whatsapp_connection_probe",
        ("desired_state", "last_probe_at"),
        False,
    ),
)


def upgrade() -> None:
    bind = op.get_bind()
    orphan_count = int(
        bind.execute(
            sa.text(
                "SELECT count(*) FROM channel_accounts "
                "WHERE provider = 'whatsapp' AND tenant_id IS NULL"
            )
        ).scalar()
        or 0
    )
    if orphan_count:
        raise RuntimeError(
            "whatsapp_channel_account_tenant_assignment_required:"
            f"{orphan_count}"
        )

    op.create_table(
        "whatsapp_connections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("channel_account_id", sa.Integer(), nullable=False),
        sa.Column("transport", sa.String(length=40), nullable=False),
        sa.Column("desired_state", sa.String(length=24), nullable=False),
        sa.Column("observed_state", sa.String(length=32), nullable=False),
        sa.Column("authentication_state", sa.String(length=24), nullable=False),
        sa.Column("listener_state", sa.String(length=24), nullable=False),
        sa.Column("verification_state", sa.String(length=32), nullable=False),
        sa.Column("desired_generation", sa.Integer(), nullable=False),
        sa.Column("observed_generation", sa.Integer(), nullable=False),
        sa.Column("phone_number", sa.String(length=80), nullable=True),
        sa.Column("jid", sa.String(length=180), nullable=True),
        sa.Column("business_account_id", sa.String(length=120), nullable=True),
        sa.Column("waba_id", sa.String(length=120), nullable=True),
        sa.Column("phone_number_id", sa.String(length=120), nullable=True),
        sa.Column("graph_api_version", sa.String(length=24), nullable=True),
        sa.Column("access_token_encrypted", sa.Text(), nullable=True),
        sa.Column("access_token_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("app_secret_encrypted", sa.Text(), nullable=True),
        sa.Column("verify_token_encrypted", sa.Text(), nullable=True),
        sa.Column("sidecar_session_key", sa.String(length=180), nullable=True),
        sa.Column("session_generation", sa.Integer(), nullable=False),
        sa.Column("last_qr_generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("qr_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_inbound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_outbound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_probe_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_probe_status", sa.String(length=40), nullable=True),
        sa.Column("reconnect_count", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(length=120), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("inbound_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outbound_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "transport IN ('baileys_sidecar','meta_cloud_api')",
            name="ck_whatsapp_connection_transport",
        ),
        sa.CheckConstraint(
            "desired_state IN ('disabled','binding','active')",
            name="ck_whatsapp_connection_desired_state",
        ),
        sa.CheckConstraint(
            "observed_state IN ("
            "'unconfigured','auth_required','qr_pending','auth_persisting',"
            "'connecting','connected','degraded','logged_out','error','disabled'"
            ")",
            name="ck_whatsapp_connection_observed_state",
        ),
        sa.CheckConstraint(
            "authentication_state IN ("
            "'unconfigured','pending','linked','unstable','revoked','error'"
            ")",
            name="ck_whatsapp_connection_authentication_state",
        ),
        sa.CheckConstraint(
            "listener_state IN ("
            "'stopped','starting','active','reconnecting','error'"
            ")",
            name="ck_whatsapp_connection_listener_state",
        ),
        sa.CheckConstraint(
            "verification_state IN ("
            "'pending','inbound_verified','outbound_verified','verified','failed'"
            ")",
            name="ck_whatsapp_connection_verification_state",
        ),
        sa.CheckConstraint(
            "desired_generation >= 0 AND observed_generation >= 0",
            name="ck_whatsapp_connection_generations_nonnegative",
        ),
        sa.CheckConstraint(
            "reconnect_count >= 0",
            name="ck_whatsapp_connection_reconnect_count_nonnegative",
        ),
        sa.CheckConstraint(
            "transport <> 'meta_cloud_api' OR phone_number_id IS NOT NULL",
            name="ck_whatsapp_meta_phone_number_id_required",
        ),
        sa.CheckConstraint(
            "transport <> 'meta_cloud_api' OR waba_id IS NOT NULL",
            name="ck_whatsapp_meta_waba_id_required",
        ),
        sa.ForeignKeyConstraint(
            ["channel_account_id"],
            ["channel_accounts.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "channel_account_id",
            name="uq_whatsapp_connections_channel_account",
        ),
        sa.UniqueConstraint(
            "phone_number_id",
            name="uq_whatsapp_connections_phone_number_id",
        ),
    )
    for name, columns, unique in _INDEXES:
        op.create_index(
            name,
            "whatsapp_connections",
            list(columns),
            unique=unique,
        )

    bind.execute(
        sa.text(
            "INSERT INTO whatsapp_connections ("
            "tenant_id, channel_account_id, transport, desired_state, "
            "observed_state, authentication_state, listener_state, "
            "verification_state, desired_generation, observed_generation, "
            "sidecar_session_key, session_generation, reconnect_count, "
            "created_at, updated_at"
            ") SELECT tenant_id, id, 'baileys_sidecar', 'disabled', "
            "'unconfigured', 'unconfigured', 'stopped', 'pending', 0, 0, "
            "account_id, 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
            "FROM channel_accounts WHERE provider = 'whatsapp'"
        )
    )


def downgrade() -> None:
    op.drop_table("whatsapp_connections")
