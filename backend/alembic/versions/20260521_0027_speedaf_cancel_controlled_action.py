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


def _table_exists(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _index_names(table: str) -> set[str]:
    if not _table_exists(table):
        return set()
    return {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    if not _table_exists("speedaf_cancel_idempotency"):
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
    names = _index_names("speedaf_cancel_idempotency")
    if "ix_speedaf_cancel_idempotency_ticket_id" not in names:
        op.create_index("ix_speedaf_cancel_idempotency_ticket_id", "speedaf_cancel_idempotency", ["ticket_id"])
    if "ix_speedaf_cancel_idempotency_status" not in names:
        op.create_index("ix_speedaf_cancel_idempotency_status", "speedaf_cancel_idempotency", ["status"])


def downgrade() -> None:
    if not _table_exists("speedaf_cancel_idempotency"):
        return
    names = _index_names("speedaf_cancel_idempotency")
    if "ix_speedaf_cancel_idempotency_status" in names:
        op.drop_index("ix_speedaf_cancel_idempotency_status", table_name="speedaf_cancel_idempotency")
    if "ix_speedaf_cancel_idempotency_ticket_id" in names:
        op.drop_index("ix_speedaf_cancel_idempotency_ticket_id", table_name="speedaf_cancel_idempotency")
    op.drop_table("speedaf_cancel_idempotency")
