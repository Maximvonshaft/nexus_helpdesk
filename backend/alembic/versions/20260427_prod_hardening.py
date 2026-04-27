"""production hardening for webchat and outbound safety

Revision ID: 20260427_prod_hardening
Revises: 20260410_0001
Create Date: 2026-04-27 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260427_prod_hardening"
down_revision = "20260426_ctrl_foundation"
branch_labels = None
depends_on = None


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _columns(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return set()
    return {col["name"] for col in inspector.get_columns(table_name)}


def _indexes(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return set()
    return {idx["name"] for idx in inspector.get_indexes(table_name)}


def _create_index_if_missing(bind, name: str, table_name: str, columns: list[str], *, unique: bool = False) -> None:
    if name not in _indexes(bind, table_name):
        op.create_index(name, table_name, columns, unique=unique)


def upgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)

    if "webchat_rate_limits" not in tables:
        op.create_table(
            "webchat_rate_limits",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("bucket_key", sa.String(length=255), nullable=False),
            sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
            sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    _create_index_if_missing(bind, "ix_webchat_rate_limits_bucket_key", "webchat_rate_limits", ["bucket_key"], unique=False)
    _create_index_if_missing(bind, "ix_webchat_rate_limits_window_start", "webchat_rate_limits", ["window_start"], unique=False)

    cols = _columns(bind, "ticket_outbound_messages")
    if "provider_idempotency_key" not in cols:
        op.add_column("ticket_outbound_messages", sa.Column("provider_idempotency_key", sa.String(length=255), nullable=True))
        _create_index_if_missing(bind, "ix_ticket_outbound_messages_provider_idempotency_key", "ticket_outbound_messages", ["provider_idempotency_key"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if "ticket_outbound_messages" in _tables(bind) and "provider_idempotency_key" in _columns(bind, "ticket_outbound_messages"):
        try:
            op.drop_index("ix_ticket_outbound_messages_provider_idempotency_key", table_name="ticket_outbound_messages")
        except Exception:
            pass
        op.drop_column("ticket_outbound_messages", "provider_idempotency_key")
    if "webchat_rate_limits" in _tables(bind):
        op.drop_table("webchat_rate_limits")
