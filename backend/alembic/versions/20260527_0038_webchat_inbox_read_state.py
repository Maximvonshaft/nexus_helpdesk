"""webchat inbox read state

Revision ID: 20260527_0038
Revises: 20260527_0037
Create Date: 2026-05-27
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260527_0038"
down_revision = "20260527_0037"
branch_labels = None
depends_on = None


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _indexes(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return set()
    return {idx["name"] for idx in inspector.get_indexes(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    if "webchat_inbox_read_states" not in _tables(bind):
        op.create_table(
            "webchat_inbox_read_states",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("webchat_conversations.id"), nullable=False),
            sa.Column("last_read_event_id", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("marked_unread", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("user_id", "conversation_id", name="uq_webchat_inbox_read_state_user_conversation"),
        )
    indexes = _indexes(bind, "webchat_inbox_read_states")
    if "ix_webchat_inbox_read_states_user_id" not in indexes:
        op.create_index("ix_webchat_inbox_read_states_user_id", "webchat_inbox_read_states", ["user_id"])
    if "ix_webchat_inbox_read_states_conversation_id" not in indexes:
        op.create_index("ix_webchat_inbox_read_states_conversation_id", "webchat_inbox_read_states", ["conversation_id"])
    if "ix_webchat_inbox_read_states_marked_unread" not in indexes:
        op.create_index("ix_webchat_inbox_read_states_marked_unread", "webchat_inbox_read_states", ["marked_unread"])
    if "ix_webchat_inbox_read_states_user_updated" not in indexes:
        op.create_index("ix_webchat_inbox_read_states_user_updated", "webchat_inbox_read_states", ["user_id", "updated_at"])


def downgrade() -> None:
    bind = op.get_bind()
    if "webchat_inbox_read_states" in _tables(bind):
        op.drop_table("webchat_inbox_read_states")
