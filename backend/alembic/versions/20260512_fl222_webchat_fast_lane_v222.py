"""webchat fast lane V2.2.2 idempotency and ticket dedupe

Revision ID: 20260512_fl222
Revises: 20260510_0022
Create Date: 2026-05-12
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260512_fl222"
down_revision = "20260510_0022"
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


def _dialect_name(bind) -> str:
    return bind.dialect.name


def upgrade() -> None:
    bind = op.get_bind()
    if "webchat_fast_idempotency" not in _tables(bind):
        op.create_table(
            "webchat_fast_idempotency",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("tenant_key", sa.String(length=120), nullable=False),
            sa.Column("session_id", sa.String(length=120), nullable=False),
            sa.Column("client_message_id", sa.String(length=120), nullable=False),
            sa.Column("request_hash", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="processing"),
            sa.Column("response_json", sa.JSON(), nullable=True),
            sa.Column("error_code", sa.String(length=120), nullable=True),
            sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("owner_request_id", sa.String(length=160), nullable=True),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("tenant_key", "session_id", "client_message_id", name="uq_webchat_fast_idem_key"),
        )
    for name, cols in {
        "ix_webchat_fast_idempotency_tenant_key": ["tenant_key"],
        "ix_webchat_fast_idempotency_session_id": ["session_id"],
        "ix_webchat_fast_idempotency_client_message_id": ["client_message_id"],
        "ix_webchat_fast_idempotency_request_hash": ["request_hash"],
        "ix_webchat_fast_idempotency_status": ["status"],
        "ix_webchat_fast_idempotency_locked_until": ["locked_until"],
        "ix_webchat_fast_idempotency_owner_request_id": ["owner_request_id"],
        "ix_webchat_fast_idempotency_error_code": ["error_code"],
        "ix_webchat_fast_idempotency_created_at": ["created_at"],
        "ix_webchat_fast_idempotency_updated_at": ["updated_at"],
        "ix_webchat_fast_idempotency_expires_at": ["expires_at"],
    }.items():
        if name not in _indexes(bind, "webchat_fast_idempotency"):
            op.create_index(name, "webchat_fast_idempotency", cols)

    if "tickets" in _tables(bind) and "source_dedupe_key" not in _columns(bind, "tickets"):
        op.add_column("tickets", sa.Column("source_dedupe_key", sa.String(length=300), nullable=True))
    if "tickets" in _tables(bind) and "ux_tickets_source_dedupe_key" not in _indexes(bind, "tickets"):
        if _dialect_name(bind) == "postgresql":
            op.create_index(
                "ux_tickets_source_dedupe_key",
                "tickets",
                ["source_dedupe_key"],
                unique=True,
                postgresql_where=sa.text("source_dedupe_key IS NOT NULL"),
            )
        else:
            op.create_index("ux_tickets_source_dedupe_key", "tickets", ["source_dedupe_key"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    if "tickets" in _tables(bind):
        if "ux_tickets_source_dedupe_key" in _indexes(bind, "tickets"):
            op.drop_index("ux_tickets_source_dedupe_key", table_name="tickets")
        if "source_dedupe_key" in _columns(bind, "tickets"):
            op.drop_column("tickets", "source_dedupe_key")
    if "webchat_fast_idempotency" in _tables(bind):
        op.drop_table("webchat_fast_idempotency")
