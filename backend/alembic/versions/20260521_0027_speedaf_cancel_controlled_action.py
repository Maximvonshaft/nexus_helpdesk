"""speedaf cancel controlled action

Revision ID: 20260521_0027
Revises: 20260520_0026
Create Date: 2026-05-21
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260521_0027"
down_revision = "20260520_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "speedaf_cancel_idempotency",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("waybill_hash", sa.String(length=64), nullable=False),
        sa.Column("reason_code", sa.String(length=16), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("request_id", sa.String(length=160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("dedupe_key", name="ux_speedaf_cancel_idempotency_dedupe_key"),
    )
    op.create_index("ix_speedaf_cancel_idempotency_ticket_id", "speedaf_cancel_idempotency", ["ticket_id"])
    op.create_index("ix_speedaf_cancel_idempotency_status", "speedaf_cancel_idempotency", ["status"])


def downgrade() -> None:
    pass
