"""Add encrypted Embedded Signup post-OAuth exchange checkpoint.

Revision ID: 20260729_wa5_signup_checkpoint
Revises: 20260728_r6_routing
Create Date: 2026-07-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260729_wa5_signup_checkpoint"
down_revision = "20260728_r6_routing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "whatsapp_embedded_signup_exchange_checkpoints",
        sa.Column(
            "session_id",
            sa.String(length=64),
            sa.ForeignKey(
                "whatsapp_embedded_signup_sessions.id",
                ondelete="CASCADE",
            ),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("encrypted_access_token", sa.Text(), nullable=False),
        sa.Column(
            "exchanged_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("whatsapp_embedded_signup_exchange_checkpoints")
