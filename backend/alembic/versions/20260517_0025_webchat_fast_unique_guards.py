"""webchat fast unique guards

Revision ID: 20260517_0025
Revises: 20260516_0024
Create Date: 2026-05-17
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260517_0025"
down_revision = "20260516_0024"
branch_labels = None
depends_on = None


FAST_OPEN_SESSION_INDEX = "uq_webchat_fast_open_session"
MESSAGE_CLIENT_INDEX = "uq_webchat_msg_conversation_client"


def _index_exists(name: str, table: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return name in {idx["name"] for idx in inspector.get_indexes(table)}


def _raise_if_duplicate_fast_sessions() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT tenant_key, channel_key, fast_session_id, origin, status, COUNT(*) AS row_count
            FROM webchat_conversations
            WHERE origin = 'webchat-fast'
              AND status = 'open'
              AND fast_session_id IS NOT NULL
            GROUP BY tenant_key, channel_key, fast_session_id, origin, status
            HAVING COUNT(*) > 1
            LIMIT 20
            """
        )
    ).mappings().all()
    if rows:
        samples = [dict(row) for row in rows]
        raise RuntimeError(f"duplicate open WebChat Fast sessions must be resolved before adding {FAST_OPEN_SESSION_INDEX}: {samples}")


def _raise_if_duplicate_client_messages() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT conversation_id, client_message_id, COUNT(*) AS row_count
            FROM webchat_messages
            WHERE client_message_id IS NOT NULL
            GROUP BY conversation_id, client_message_id
            HAVING COUNT(*) > 1
            LIMIT 20
            """
        )
    ).mappings().all()
    if rows:
        samples = [dict(row) for row in rows]
        raise RuntimeError(f"duplicate WebChat messages must be resolved before adding {MESSAGE_CLIENT_INDEX}: {samples}")


def _create_partial_unique_index_if_missing(name: str, table: str, columns_sql: str, where_sql: str) -> None:
    if _index_exists(name, table):
        return
    op.execute(sa.text(f"CREATE UNIQUE INDEX {name} ON {table} ({columns_sql}) WHERE {where_sql}"))


def _drop_index_if_exists(name: str, table: str) -> None:
    if _index_exists(name, table):
        op.drop_index(name, table_name=table)


def upgrade() -> None:
    _raise_if_duplicate_fast_sessions()
    _raise_if_duplicate_client_messages()
    _create_partial_unique_index_if_missing(
        FAST_OPEN_SESSION_INDEX,
        "webchat_conversations",
        "tenant_key, channel_key, fast_session_id, origin",
        "status = 'open' AND fast_session_id IS NOT NULL",
    )
    _create_partial_unique_index_if_missing(
        MESSAGE_CLIENT_INDEX,
        "webchat_messages",
        "conversation_id, client_message_id",
        "client_message_id IS NOT NULL",
    )


def downgrade() -> None:
    _drop_index_if_exists(MESSAGE_CLIENT_INDEX, "webchat_messages")
    _drop_index_if_exists(FAST_OPEN_SESSION_INDEX, "webchat_conversations")
