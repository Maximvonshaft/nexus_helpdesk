"""Add WhatsApp media evidence and Meta Embedded Signup sessions.

Revision ID: 20260727_wa2
Revises: 20260727_wa1
Create Date: 2026-07-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260727_wa2"
down_revision = "20260727_wa1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "whatsapp_media_assets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("inbound_message_id", sa.Integer(), nullable=True),
        sa.Column("outbound_message_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("provider_media_id", sa.String(length=255), nullable=False),
        sa.Column("media_kind", sa.String(length=40), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=True),
        sa.Column("declared_mime_type", sa.String(length=160), nullable=True),
        sa.Column("detected_mime_type", sa.String(length=160), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("storage_status", sa.String(length=32), nullable=False),
        sa.Column("scan_status", sa.String(length=24), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=True),
        sa.Column("ticket_attachment_id", sa.Integer(), nullable=True),
        sa.Column("provider_url_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=120), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "provider IN ('baileys','meta')",
            name="ck_whatsapp_media_provider",
        ),
        sa.CheckConstraint(
            "storage_status IN ("
            "'pending','downloading','scanning','available','quarantined',"
            "'rejected','failed','deleted'"
            ")",
            name="ck_whatsapp_media_storage_status",
        ),
        sa.CheckConstraint(
            "scan_status IN ('pending','clean','infected','unavailable','failed')",
            name="ck_whatsapp_media_scan_status",
        ),
        sa.CheckConstraint(
            "byte_size IS NULL OR byte_size >= 0",
            name="ck_whatsapp_media_byte_size_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["whatsapp_connections.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["inbound_message_id"],
            ["whatsapp_inbound_messages.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["outbound_message_id"],
            ["ticket_outbound_messages.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ticket_attachment_id"],
            ["ticket_attachments.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "connection_id",
            "provider",
            "provider_media_id",
            name="uq_whatsapp_media_connection_provider_id",
        ),
    )
    for name, columns, unique in (
        ("ix_whatsapp_media_assets_tenant_id", ["tenant_id"], False),
        ("ix_whatsapp_media_assets_connection_id", ["connection_id"], False),
        ("ix_whatsapp_media_assets_inbound_message_id", ["inbound_message_id"], False),
        ("ix_whatsapp_media_assets_outbound_message_id", ["outbound_message_id"], False),
        ("ix_whatsapp_media_assets_provider", ["provider"], False),
        ("ix_whatsapp_media_assets_provider_media_id", ["provider_media_id"], False),
        ("ix_whatsapp_media_assets_media_kind", ["media_kind"], False),
        ("ix_whatsapp_media_assets_sha256", ["sha256"], False),
        ("ix_whatsapp_media_assets_storage_status", ["storage_status"], False),
        ("ix_whatsapp_media_assets_scan_status", ["scan_status"], False),
        ("ix_whatsapp_media_assets_storage_key", ["storage_key"], False),
        ("ix_whatsapp_media_assets_ticket_attachment_id", ["ticket_attachment_id"], False),
        ("ix_whatsapp_media_assets_created_at", ["created_at"], False),
        ("ix_whatsapp_media_assets_updated_at", ["updated_at"], False),
        (
            "ix_whatsapp_media_tenant_status",
            ["tenant_id", "storage_status", "created_at"],
            False,
        ),
    ):
        op.create_index(name, "whatsapp_media_assets", columns, unique=unique)

    op.create_table(
        "whatsapp_embedded_signup_sessions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("requested_by", sa.Integer(), nullable=False),
        sa.Column("state_digest", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("connection_id", sa.Integer(), nullable=True),
        sa.Column("code_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("business_account_id", sa.String(length=120), nullable=True),
        sa.Column("waba_id", sa.String(length=120), nullable=True),
        sa.Column("phone_number_id", sa.String(length=120), nullable=True),
        sa.Column("last_error_code", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ("
            "'pending','exchanging','completed','expired','failed','cancelled'"
            ")",
            name="ck_whatsapp_embedded_signup_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["whatsapp_connections.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state_digest"),
        sa.UniqueConstraint("connection_id"),
    )
    for name, columns, unique in (
        ("ix_whatsapp_embedded_signup_sessions_tenant_id", ["tenant_id"], False),
        ("ix_whatsapp_embedded_signup_sessions_requested_by", ["requested_by"], False),
        ("ix_whatsapp_embedded_signup_sessions_status", ["status"], False),
        ("ix_whatsapp_embedded_signup_sessions_expires_at", ["expires_at"], False),
        ("ix_whatsapp_embedded_signup_sessions_connection_id", ["connection_id"], True),
        ("ix_whatsapp_embedded_signup_sessions_created_at", ["created_at"], False),
        ("ix_whatsapp_embedded_signup_sessions_updated_at", ["updated_at"], False),
        (
            "ix_whatsapp_embedded_signup_tenant_status",
            ["tenant_id", "status", "expires_at"],
            False,
        ),
    ):
        op.create_index(
            name,
            "whatsapp_embedded_signup_sessions",
            columns,
            unique=unique,
        )


def downgrade() -> None:
    op.drop_table("whatsapp_embedded_signup_sessions")
    op.drop_table("whatsapp_media_assets")
