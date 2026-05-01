"""webchat structured cards and actions

Revision ID: 20260502_wc_cards
Revises: 20260501_0012
Create Date: 2026-05-02
"""
from alembic import op
import sqlalchemy as sa

revision = "20260502_wc_cards"
down_revision = "20260501_0012"
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


def _create_index_if_missing(bind, name: str, table: str, cols: list[str]) -> None:
    if name not in _indexes(bind, table):
        op.create_index(name, table, cols)


def upgrade() -> None:
    bind = op.get_bind()
    if "webchat_messages" in _tables(bind):
        _add_column_if_missing(bind, "webchat_messages", sa.Column("message_type", sa.String(length=32), nullable=False, server_default="text"))
        _add_column_if_missing(bind, "webchat_messages", sa.Column("body_text", sa.Text(), nullable=True))
        _add_column_if_missing(bind, "webchat_messages", sa.Column("payload_json", sa.Text(), nullable=True))
        _add_column_if_missing(bind, "webchat_messages", sa.Column("metadata_json", sa.Text(), nullable=True))
        _add_column_if_missing(bind, "webchat_messages", sa.Column("client_message_id", sa.String(length=120), nullable=True))
        _add_column_if_missing(bind, "webchat_messages", sa.Column("delivery_status", sa.String(length=32), nullable=False, server_default="sent"))
        _add_column_if_missing(bind, "webchat_messages", sa.Column("action_status", sa.String(length=32), nullable=True))
        _create_index_if_missing(bind, "ix_webchat_messages_message_type", "webchat_messages", ["message_type"])
        _create_index_if_missing(bind, "ix_webchat_messages_client_message_id", "webchat_messages", ["client_message_id"])
        _create_index_if_missing(bind, "ix_webchat_messages_delivery_status", "webchat_messages", ["delivery_status"])
        _create_index_if_missing(bind, "ix_webchat_messages_action_status", "webchat_messages", ["action_status"])

    if "webchat_card_actions" not in _tables(bind):
        op.create_table(
            "webchat_card_actions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("webchat_conversations.id"), nullable=False),
            sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id"), nullable=False),
            sa.Column("message_id", sa.Integer(), sa.ForeignKey("webchat_messages.id"), nullable=False),
            sa.Column("action_type", sa.String(length=64), nullable=False),
            sa.Column("action_payload_json", sa.Text(), nullable=False),
            sa.Column("submitted_by", sa.String(length=64), nullable=False, server_default="visitor"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="submitted"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("ip_hash", sa.String(length=96), nullable=True),
            sa.Column("user_agent_hash", sa.String(length=96), nullable=True),
            sa.Column("origin", sa.String(length=255), nullable=True),
        )
    _create_index_if_missing(bind, "ix_webchat_card_actions_conversation_id", "webchat_card_actions", ["conversation_id"])
    _create_index_if_missing(bind, "ix_webchat_card_actions_ticket_id", "webchat_card_actions", ["ticket_id"])
    _create_index_if_missing(bind, "ix_webchat_card_actions_message_id", "webchat_card_actions", ["message_id"])
    _create_index_if_missing(bind, "ix_webchat_card_actions_status", "webchat_card_actions", ["status"])
    _create_index_if_missing(bind, "ix_webchat_card_actions_action_type", "webchat_card_actions", ["action_type"])


def downgrade() -> None:
    bind = op.get_bind()
    if "webchat_card_actions" in _tables(bind):
        op.drop_table("webchat_card_actions")
    if "webchat_messages" in _tables(bind):
        for name in [
            "ix_webchat_messages_action_status",
            "ix_webchat_messages_delivery_status",
            "ix_webchat_messages_client_message_id",
            "ix_webchat_messages_message_type",
        ]:
            if name in _indexes(bind, "webchat_messages"):
                op.drop_index(name, table_name="webchat_messages")
        existing_cols = _columns(bind, "webchat_messages")
        with op.batch_alter_table("webchat_messages") as batch_op:
            for col in ["action_status", "delivery_status", "client_message_id", "metadata_json", "payload_json", "body_text", "message_type"]:
                if col in existing_cols:
                    batch_op.drop_column(col)
