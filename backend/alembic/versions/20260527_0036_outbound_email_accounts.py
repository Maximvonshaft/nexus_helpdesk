"""outbound email account registry

Revision ID: 20260527_0036
Revises: 20260527_0035
Create Date: 2026-05-27
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260527_0036"
down_revision = "20260527_0035"
branch_labels = None
depends_on = None


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _indexes(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return set()
    return {idx["name"] for idx in inspector.get_indexes(table_name)}


def _create_index_once(name: str, table_name: str, columns: list[str], *, unique: bool = False, **kwargs) -> None:
    bind = op.get_bind()
    if table_name in _tables(bind) and name not in _indexes(bind, table_name):
        op.create_index(name, table_name, columns, unique=unique, **kwargs)


def upgrade() -> None:
    bind = op.get_bind()
    if "outbound_email_accounts" not in _tables(bind):
        op.create_table(
            "outbound_email_accounts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("display_name", sa.String(length=160), nullable=True),
            sa.Column("host", sa.String(length=253), nullable=False),
            sa.Column("port", sa.Integer(), nullable=False),
            sa.Column("username", sa.String(length=255), nullable=False),
            sa.Column("password_encrypted", sa.Text(), nullable=False),
            sa.Column("from_address", sa.String(length=320), nullable=False),
            sa.Column("reply_to", sa.String(length=320), nullable=True),
            sa.Column("security_mode", sa.String(length=20), nullable=False, server_default="starttls"),
            sa.Column("market_id", sa.Integer(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
            sa.Column("health_status", sa.String(length=40), nullable=False, server_default="unknown"),
            sa.Column("last_test_status", sa.String(length=40), nullable=True),
            sa.Column("last_test_error", sa.Text(), nullable=True),
            sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("updated_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["market_id"], ["markets.id"]),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
            sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        )

    for name, cols in {
        "ix_outbound_email_accounts_host": ["host"],
        "ix_outbound_email_accounts_from_address": ["from_address"],
        "ix_outbound_email_accounts_security_mode": ["security_mode"],
        "ix_outbound_email_accounts_market_id": ["market_id"],
        "ix_outbound_email_accounts_is_active": ["is_active"],
        "ix_outbound_email_accounts_health_status": ["health_status"],
        "ix_outbound_email_accounts_last_test_at": ["last_test_at"],
        "ix_outbound_email_accounts_created_by": ["created_by"],
        "ix_outbound_email_accounts_updated_by": ["updated_by"],
        "ix_outbound_email_accounts_market_active_priority": ["market_id", "is_active", "priority"],
    }.items():
        _create_index_once(name, "outbound_email_accounts", cols)

    _create_index_once(
        "uq_outbound_email_accounts_global_route",
        "outbound_email_accounts",
        ["host", "port", "username", "from_address"],
        unique=True,
        postgresql_where=sa.text("market_id IS NULL"),
        sqlite_where=sa.text("market_id IS NULL"),
    )
    _create_index_once(
        "uq_outbound_email_accounts_market_route",
        "outbound_email_accounts",
        ["host", "port", "username", "from_address", "market_id"],
        unique=True,
        postgresql_where=sa.text("market_id IS NOT NULL"),
        sqlite_where=sa.text("market_id IS NOT NULL"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if "outbound_email_accounts" in _tables(bind):
        op.drop_table("outbound_email_accounts")
