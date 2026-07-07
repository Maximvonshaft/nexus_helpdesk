"""persist safe WebChat AI turn runtime trace

Revision ID: 20260704_0049
Revises: 20260630_0048
Create Date: 2026-07-04
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260704_0049"
down_revision = "20260630_0048"
branch_labels = None
depends_on = None


def _columns(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return set()
    return {col["name"] for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    if "runtime_trace_json" not in _columns(bind, "webchat_ai_turns"):
        op.add_column("webchat_ai_turns", sa.Column("runtime_trace_json", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if "runtime_trace_json" in _columns(bind, "webchat_ai_turns"):
        op.drop_column("webchat_ai_turns", "runtime_trace_json")
