"""integration request log request id

Revision ID: 20260529_0039
Revises: 20260527_0038
Create Date: 2026-05-29
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260529_0039"
down_revision = "20260527_0038"
branch_labels = None
depends_on = None


def _columns(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _indexes(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return set()
    return {idx["name"] for idx in inspector.get_indexes(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    if "request_id" not in _columns(bind, "integration_request_logs"):
        op.add_column("integration_request_logs", sa.Column("request_id", sa.String(length=120), nullable=True))
    if "ix_integration_request_logs_request_id" not in _indexes(bind, "integration_request_logs"):
        op.create_index("ix_integration_request_logs_request_id", "integration_request_logs", ["request_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if "ix_integration_request_logs_request_id" in _indexes(bind, "integration_request_logs"):
        op.drop_index("ix_integration_request_logs_request_id", table_name="integration_request_logs")
    if "request_id" in _columns(bind, "integration_request_logs"):
        op.drop_column("integration_request_logs", "request_id")
