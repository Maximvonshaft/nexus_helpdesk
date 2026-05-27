"""outbound email runtime fields

Revision ID: 20260527_0037
Revises: 20260527_0036
Create Date: 2026-05-27
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260527_0037"
down_revision = "20260527_0036"
branch_labels = None
depends_on = None


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _columns(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return set()
    return {col["name"] for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    if "ticket_outbound_messages" in _tables(bind) and "subject" not in _columns(bind, "ticket_outbound_messages"):
        op.add_column("ticket_outbound_messages", sa.Column("subject", sa.String(length=255), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if "ticket_outbound_messages" in _tables(bind) and "subject" in _columns(bind, "ticket_outbound_messages"):
        op.drop_column("ticket_outbound_messages", "subject")
