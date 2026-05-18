"""webchat fast unique guards

Revision ID: 20260518_0025
Revises: 20260516_0024
Create Date: 2026-05-18
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260518_0025"
down_revision = "20260516_0024"
branch_labels = None
depends_on = None


def _dialect_name() -> str:
    return op.get_bind().dialect.name


def _index_exists(name: str, table: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return name in {idx["name"] for idx in inspector.get_indexes(table)}


def _drop_index_if_exists(name: str, table: str) -> None:
    if _index_exists(name, table):
        op.drop_index(name, table_name=table)


def _create_partial_unique_index_if_missing(name: str, table: str, columns: list[str], where_sql: str) -> None:
    if _index_exists(name, table):
        return
    dialect = _dialect_name()
    kwargs = {"unique": True}
    if dialect == "postgresql":
        kwargs["postgresql_where"] = sa.text(where_sql)
    elif dialect == "sqlite":
        kwargs["sqlite_where"] = sa.text(where_sql)
    else:
        # Partial unique indexes are required for this guard. Avoid creating an
        # over-broad full unique index on unsupported dialects.
        return
    op.create_index(name, table, columns, **kwargs)


def _normalize_duplicate_open_fast_conversations() -> None:
    """Make the new open-session unique guard deployable without deleting data."""

    dialect = _dialect_name()
    if dialect == "postgresql":
        op.execute(
            sa.text(
                """
                WITH ranked AS (
                    SELECT id,
                           row_number() OVER (
                               PARTITION BY tenant_key, channel_key, fast_session_id, origin
                               ORDER BY id ASC
                           ) AS rn
                    FROM webchat_conversations
                    WHERE fast_session_id IS NOT NULL
                      AND status = 'open'
                )
                UPDATE webchat_conversations AS c
                SET status = 'merged_duplicate',
                    updated_at = now()
                FROM ranked AS r
                WHERE c.id = r.id
                  AND r.rn > 1
                """
            )
        )
        return
    if dialect == "sqlite":
        op.execute(
            sa.text(
                """
                WITH ranked AS (
                    SELECT id,
                           row_number() OVER (
                               PARTITION BY tenant_key, channel_key, fast_session_id, origin
                               ORDER BY id ASC
                           ) AS rn
                    FROM webchat_conversations
                    WHERE fast_session_id IS NOT NULL
                      AND status = 'open'
                )
                UPDATE webchat_conversations
                SET status = 'merged_duplicate',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
                """
            )
        )


def _normalize_duplicate_client_message_ids() -> None:
    """Preserve duplicate rows but make duplicate client ids unique before indexing."""

    dialect = _dialect_name()
    if dialect == "postgresql":
        op.execute(
            sa.text(
                """
                WITH ranked AS (
                    SELECT id,
                           row_number() OVER (
                               PARTITION BY conversation_id, client_message_id
                               ORDER BY id ASC
                           ) AS rn
                    FROM webchat_messages
                    WHERE client_message_id IS NOT NULL
                )
                UPDATE webchat_messages AS m
                SET client_message_id = left(m.client_message_id, 96) || ':dup:' || m.id::text
                FROM ranked AS r
                WHERE m.id = r.id
                  AND r.rn > 1
                """
            )
        )
        return
    if dialect == "sqlite":
        op.execute(
            sa.text(
                """
                WITH ranked AS (
                    SELECT id,
                           row_number() OVER (
                               PARTITION BY conversation_id, client_message_id
                               ORDER BY id ASC
                           ) AS rn
                    FROM webchat_messages
                    WHERE client_message_id IS NOT NULL
                )
                UPDATE webchat_messages
                SET client_message_id = substr(client_message_id, 1, 96) || ':dup:' || id
                WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
                """
            )
        )


def upgrade() -> None:
    _normalize_duplicate_open_fast_conversations()
    _normalize_duplicate_client_message_ids()

    _create_partial_unique_index_if_missing(
        "uq_webchat_fast_open_session",
        "webchat_conversations",
        ["tenant_key", "channel_key", "fast_session_id", "origin"],
        "fast_session_id IS NOT NULL AND status = 'open'",
    )
    _create_partial_unique_index_if_missing(
        "uq_webchat_messages_conversation_client",
        "webchat_messages",
        ["conversation_id", "client_message_id"],
        "client_message_id IS NOT NULL",
    )


def downgrade() -> None:
    _drop_index_if_exists("uq_webchat_messages_conversation_client", "webchat_messages")
    _drop_index_if_exists("uq_webchat_fast_open_session", "webchat_conversations")
