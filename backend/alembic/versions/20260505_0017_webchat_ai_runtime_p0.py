"""webchat ai runtime p0 idempotency hardening

Revision ID: 20260505_0017
Revises: 20260503_0016
Create Date: 2026-05-05
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260505_0017"
down_revision = "20260503_0016"
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


def _add_column_if_missing(bind, table: str, column: sa.Column) -> None:
    if column.name not in _columns(bind, table):
        op.add_column(table, column)


def _create_index_if_missing(bind, name: str, table: str, cols: list[str], *, unique: bool = False, **kwargs) -> None:
    if name not in _indexes(bind, table):
        op.create_index(name, table, cols, unique=unique, **kwargs)


def _has_duplicate_visitor_client_messages(bind) -> bool:
    if bind.dialect.name.startswith("postgresql"):
        sql = sa.text(
            """
            SELECT 1
            FROM webchat_messages
            WHERE client_message_id IS NOT NULL AND direction = 'visitor'
            GROUP BY conversation_id, client_message_id, direction
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    else:
        sql = sa.text(
            """
            SELECT 1
            FROM webchat_messages
            WHERE client_message_id IS NOT NULL AND direction = 'visitor'
            GROUP BY conversation_id, client_message_id, direction
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    return bind.execute(sql).first() is not None


def _has_duplicate_agent_ai_turns(bind) -> bool:
    if "ai_turn_id" not in _columns(bind, "webchat_messages"):
        return False
    sql = sa.text(
        """
        SELECT 1
        FROM webchat_messages
        WHERE ai_turn_id IS NOT NULL AND direction = 'agent'
        GROUP BY ai_turn_id
        HAVING COUNT(*) > 1
        LIMIT 1
        """
    )
    return bind.execute(sql).first() is not None


def upgrade() -> None:
    bind = op.get_bind()
    if "webchat_messages" not in _tables(bind):
        return

    if _has_duplicate_visitor_client_messages(bind):
        raise RuntimeError(
            "Cannot create uq_webchat_visitor_client_message: duplicate visitor client_message_id rows exist. "
            "Run the read-only duplicate detection SQL and clean/merge historical duplicates first."
        )

    _add_column_if_missing(
        bind,
        "webchat_messages",
        sa.Column("ai_turn_id", sa.Integer(), sa.ForeignKey("webchat_ai_turns.id"), nullable=True),
    )
    _create_index_if_missing(bind, "ix_webchat_messages_ai_turn_id", "webchat_messages", ["ai_turn_id"])

    if _has_duplicate_agent_ai_turns(bind):
        raise RuntimeError(
            "Cannot create uq_webchat_agent_ai_turn: duplicate agent replies already reference the same ai_turn_id. "
            "Run the read-only duplicate detection SQL and clean/merge historical duplicates first."
        )

    dialect = bind.dialect.name
    visitor_where = sa.text("client_message_id IS NOT NULL AND direction = 'visitor'")
    agent_where = sa.text("ai_turn_id IS NOT NULL AND direction = 'agent'")
    visitor_kwargs = {}
    agent_kwargs = {}
    if dialect.startswith("postgresql"):
        visitor_kwargs["postgresql_where"] = visitor_where
        agent_kwargs["postgresql_where"] = agent_where
    elif dialect == "sqlite":
        visitor_kwargs["sqlite_where"] = visitor_where
        agent_kwargs["sqlite_where"] = agent_where

    _create_index_if_missing(
        bind,
        "uq_webchat_visitor_client_message",
        "webchat_messages",
        ["conversation_id", "client_message_id", "direction"],
        unique=True,
        **visitor_kwargs,
    )
    _create_index_if_missing(
        bind,
        "uq_webchat_agent_ai_turn",
        "webchat_messages",
        ["ai_turn_id"],
        unique=True,
        **agent_kwargs,
    )


def downgrade() -> None:
    bind = op.get_bind()
    if "webchat_messages" not in _tables(bind):
        return
    indexes = _indexes(bind, "webchat_messages")
    for name in ["uq_webchat_agent_ai_turn", "uq_webchat_visitor_client_message", "ix_webchat_messages_ai_turn_id"]:
        if name in indexes:
            op.drop_index(name, table_name="webchat_messages")
    if "ai_turn_id" in _columns(bind, "webchat_messages"):
        with op.batch_alter_table("webchat_messages") as batch_op:
            batch_op.drop_column("ai_turn_id")
