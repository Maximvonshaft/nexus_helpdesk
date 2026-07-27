"""Add ordered WhatsApp provider parts under the canonical Outbox message.

Revision ID: 20260727_wa4
Revises: 20260727_wa3
Create Date: 2026-07-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260727_wa4"
down_revision = "20260727_wa3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "whatsapp_outbound_parts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("outbound_message_id", sa.Integer(), nullable=False),
        sa.Column("attachment_id", sa.Integer(), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("part_type", sa.String(length=20), nullable=False),
        sa.Column("media_kind", sa.String(length=40), nullable=True),
        sa.Column("media_type", sa.String(length=160), nullable=True),
        sa.Column("file_name", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key", sa.String(length=180), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("provider_media_id", sa.String(length=255), nullable=True),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("failure_code", sa.String(length=120), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("receipt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "part_type IN ('text','media')",
            name="ck_whatsapp_outbound_part_type",
        ),
        sa.CheckConstraint(
            "status IN ("
            "'queued','accepted','sent','delivered','read','failed','expired','revoked'"
            ")",
            name="ck_whatsapp_outbound_part_status",
        ),
        sa.CheckConstraint(
            "sequence >= 0",
            name="ck_whatsapp_outbound_part_sequence_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["whatsapp_connections.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["outbound_message_id"],
            ["ticket_outbound_messages.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["attachment_id"],
            ["ticket_attachments.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "outbound_message_id",
            "sequence",
            name="uq_whatsapp_outbound_part_sequence",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_whatsapp_outbound_part_idempotency",
        ),
        sa.UniqueConstraint("provider_message_id"),
    )
    for name, columns, unique in (
        ("ix_whatsapp_outbound_parts_tenant_id", ["tenant_id"], False),
        ("ix_whatsapp_outbound_parts_connection_id", ["connection_id"], False),
        ("ix_whatsapp_outbound_parts_outbound_message_id", ["outbound_message_id"], False),
        ("ix_whatsapp_outbound_parts_attachment_id", ["attachment_id"], False),
        ("ix_whatsapp_outbound_parts_part_type", ["part_type"], False),
        ("ix_whatsapp_outbound_parts_idempotency_key", ["idempotency_key"], False),
        ("ix_whatsapp_outbound_parts_status", ["status"], False),
        ("ix_whatsapp_outbound_parts_provider_message_id", ["provider_message_id"], True),
        ("ix_whatsapp_outbound_parts_created_at", ["created_at"], False),
        ("ix_whatsapp_outbound_parts_updated_at", ["updated_at"], False),
        (
            "ix_whatsapp_outbound_part_parent_status",
            ["outbound_message_id", "status"],
            False,
        ),
    ):
        op.create_index(name, "whatsapp_outbound_parts", columns, unique=unique)


def downgrade() -> None:
    op.drop_table("whatsapp_outbound_parts")
