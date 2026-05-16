"""webchat fast session continuity

Revision ID: 20260516_0024
Revises: 20260514_0023
Create Date: 2026-05-16
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260516_0024"
down_revision = "20260514_0023"
branch_labels = None
depends_on = None


def _add_column_if_missing(table: str, column: sa.Column) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns(table)}
    if column.name not in existing:
        op.add_column(table, column)


def _create_index_if_missing(name: str, table: str, columns: list[str]) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {idx["name"] for idx in inspector.get_indexes(table)}
    if name not in existing:
        op.create_index(name, table, columns)


def _drop_index_if_exists(name: str, table: str) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {idx["name"] for idx in inspector.get_indexes(table)}
    if name in existing:
        op.drop_index(name, table_name=table)


def upgrade() -> None:
    _add_column_if_missing("webchat_conversations", sa.Column("fast_session_id", sa.String(length=120), nullable=True))
    _add_column_if_missing("webchat_conversations", sa.Column("fast_issue_key", sa.String(length=240), nullable=True))
    _add_column_if_missing("webchat_conversations", sa.Column("last_intent", sa.String(length=120), nullable=True))
    _add_column_if_missing("webchat_conversations", sa.Column("last_tracking_number", sa.String(length=120), nullable=True))
    _add_column_if_missing("webchat_conversations", sa.Column("fast_last_client_message_id", sa.String(length=120), nullable=True))
    _add_column_if_missing("webchat_conversations", sa.Column("fast_context_updated_at", sa.DateTime(timezone=True), nullable=True))

    with op.batch_alter_table("webchat_conversations") as batch_op:
        batch_op.alter_column("ticket_id", existing_type=sa.Integer(), nullable=True)
    with op.batch_alter_table("webchat_messages") as batch_op:
        batch_op.alter_column("ticket_id", existing_type=sa.Integer(), nullable=True)

    _create_index_if_missing("ix_webchat_fast_session", "webchat_conversations", ["tenant_key", "channel_key", "fast_session_id"])
    _create_index_if_missing("ix_webchat_fast_issue_key", "webchat_conversations", ["tenant_key", "channel_key", "fast_issue_key"])
    _create_index_if_missing("ix_webchat_fast_last_tracking", "webchat_conversations", ["last_tracking_number"])
    _create_index_if_missing("ix_webchat_fast_last_intent", "webchat_conversations", ["last_intent"])
    _create_index_if_missing("ix_webchat_fast_last_client_message", "webchat_conversations", ["fast_last_client_message_id"])
    _create_index_if_missing("ix_webchat_fast_context_updated_at", "webchat_conversations", ["fast_context_updated_at"])
    _create_index_if_missing("ix_webchat_messages_conversation_client", "webchat_messages", ["conversation_id", "client_message_id"])


def downgrade() -> None:
    _drop_index_if_exists("ix_webchat_messages_conversation_client", "webchat_messages")
    _drop_index_if_exists("ix_webchat_fast_context_updated_at", "webchat_conversations")
    _drop_index_if_exists("ix_webchat_fast_last_client_message", "webchat_conversations")
    _drop_index_if_exists("ix_webchat_fast_last_intent", "webchat_conversations")
    _drop_index_if_exists("ix_webchat_fast_last_tracking", "webchat_conversations")
    _drop_index_if_exists("ix_webchat_fast_issue_key", "webchat_conversations")
    _drop_index_if_exists("ix_webchat_fast_session", "webchat_conversations")

    with op.batch_alter_table("webchat_messages") as batch_op:
        batch_op.alter_column("ticket_id", existing_type=sa.Integer(), nullable=False)
    with op.batch_alter_table("webchat_conversations") as batch_op:
        batch_op.alter_column("ticket_id", existing_type=sa.Integer(), nullable=False)
        for column_name in (
            "fast_context_updated_at",
            "fast_last_client_message_id",
            "last_tracking_number",
            "last_intent",
            "fast_issue_key",
            "fast_session_id",
        ):
            batch_op.drop_column(column_name)
