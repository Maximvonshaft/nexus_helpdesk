"""Add bounded processing leases to canonical WhatsApp media assets.

Revision ID: 20260727_wa3
Revises: 20260727_wa2
Create Date: 2026-07-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260727_wa3"
down_revision = "20260727_wa2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "whatsapp_media_assets",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "whatsapp_media_assets",
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
    )
    op.add_column(
        "whatsapp_media_assets",
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "whatsapp_media_assets",
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "whatsapp_media_assets",
        sa.Column("locked_by", sa.String(length=120), nullable=True),
    )
    op.create_check_constraint(
        "ck_whatsapp_media_attempts_valid",
        "whatsapp_media_assets",
        "attempt_count >= 0 AND max_attempts >= 1",
    )
    op.create_index(
        "ix_whatsapp_media_assets_next_retry_at",
        "whatsapp_media_assets",
        ["next_retry_at"],
        unique=False,
    )
    op.create_index(
        "ix_whatsapp_media_assets_locked_at",
        "whatsapp_media_assets",
        ["locked_at"],
        unique=False,
    )
    op.create_index(
        "ix_whatsapp_media_assets_locked_by",
        "whatsapp_media_assets",
        ["locked_by"],
        unique=False,
    )
    op.create_index(
        "ix_whatsapp_media_claim",
        "whatsapp_media_assets",
        ["provider", "storage_status", "next_retry_at", "locked_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_whatsapp_media_claim", table_name="whatsapp_media_assets")
    op.drop_index("ix_whatsapp_media_assets_locked_by", table_name="whatsapp_media_assets")
    op.drop_index("ix_whatsapp_media_assets_locked_at", table_name="whatsapp_media_assets")
    op.drop_index("ix_whatsapp_media_assets_next_retry_at", table_name="whatsapp_media_assets")
    op.drop_constraint(
        "ck_whatsapp_media_attempts_valid",
        "whatsapp_media_assets",
        type_="check",
    )
    op.drop_column("whatsapp_media_assets", "locked_by")
    op.drop_column("whatsapp_media_assets", "locked_at")
    op.drop_column("whatsapp_media_assets", "next_retry_at")
    op.drop_column("whatsapp_media_assets", "max_attempts")
    op.drop_column("whatsapp_media_assets", "attempt_count")
