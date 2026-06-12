"""add native whatsapp inbound persistence

Revision ID: 20260612_0017
Revises: 20260601_0047
Create Date: 2026-06-12
"""

from alembic import op
import sqlalchemy as sa

revision = "20260612_0017"
down_revision = "20260601_0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "whatsapp_inbound_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("channel_account_id", sa.Integer(), sa.ForeignKey("channel_accounts.id"), nullable=False),
        sa.Column("account_id", sa.String(length=160), nullable=False),
        sa.Column("external_message_id", sa.String(length=180), nullable=False),
        sa.Column("chat_jid", sa.String(length=180), nullable=False),
        sa.Column("sender_jid", sa.String(length=180), nullable=False),
        sa.Column("sender_phone", sa.String(length=80), nullable=True),
        sa.Column("message_type", sa.String(length=80), nullable=False, server_default="text"),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("raw_payload_json", sa.JSON(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id"), nullable=True),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("webchat_conversations.id"), nullable=True),
        sa.Column("webchat_message_id", sa.Integer(), sa.ForeignKey("webchat_messages.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("channel_account_id", "external_message_id", name="uq_whatsapp_inbound_channel_external"),
    )
    op.create_index("ix_whatsapp_inbound_messages_account_id", "whatsapp_inbound_messages", ["account_id"])
    op.create_index("ix_whatsapp_inbound_messages_channel_account_id", "whatsapp_inbound_messages", ["channel_account_id"])
    op.create_index("ix_whatsapp_inbound_messages_chat", "whatsapp_inbound_messages", ["channel_account_id", "chat_jid"])
    op.create_index("ix_whatsapp_inbound_messages_chat_jid", "whatsapp_inbound_messages", ["chat_jid"])
    op.create_index("ix_whatsapp_inbound_messages_conversation_id", "whatsapp_inbound_messages", ["conversation_id"])
    op.create_index("ix_whatsapp_inbound_messages_created_at", "whatsapp_inbound_messages", ["created_at"])
    op.create_index("ix_whatsapp_inbound_messages_external_message_id", "whatsapp_inbound_messages", ["external_message_id"])
    op.create_index("ix_whatsapp_inbound_messages_message_type", "whatsapp_inbound_messages", ["message_type"])
    op.create_index("ix_whatsapp_inbound_messages_processed_at", "whatsapp_inbound_messages", ["processed_at"])
    op.create_index("ix_whatsapp_inbound_messages_received_at", "whatsapp_inbound_messages", ["received_at"])
    op.create_index("ix_whatsapp_inbound_messages_sender_jid", "whatsapp_inbound_messages", ["sender_jid"])
    op.create_index("ix_whatsapp_inbound_messages_sender_phone", "whatsapp_inbound_messages", ["sender_phone"])
    op.create_index("ix_whatsapp_inbound_messages_ticket_id", "whatsapp_inbound_messages", ["ticket_id"])
    op.create_index("ix_whatsapp_inbound_messages_webchat_message_id", "whatsapp_inbound_messages", ["webchat_message_id"])


def downgrade() -> None:
    op.drop_index("ix_whatsapp_inbound_messages_webchat_message_id", table_name="whatsapp_inbound_messages")
    op.drop_index("ix_whatsapp_inbound_messages_ticket_id", table_name="whatsapp_inbound_messages")
    op.drop_index("ix_whatsapp_inbound_messages_sender_phone", table_name="whatsapp_inbound_messages")
    op.drop_index("ix_whatsapp_inbound_messages_sender_jid", table_name="whatsapp_inbound_messages")
    op.drop_index("ix_whatsapp_inbound_messages_received_at", table_name="whatsapp_inbound_messages")
    op.drop_index("ix_whatsapp_inbound_messages_processed_at", table_name="whatsapp_inbound_messages")
    op.drop_index("ix_whatsapp_inbound_messages_message_type", table_name="whatsapp_inbound_messages")
    op.drop_index("ix_whatsapp_inbound_messages_external_message_id", table_name="whatsapp_inbound_messages")
    op.drop_index("ix_whatsapp_inbound_messages_created_at", table_name="whatsapp_inbound_messages")
    op.drop_index("ix_whatsapp_inbound_messages_conversation_id", table_name="whatsapp_inbound_messages")
    op.drop_index("ix_whatsapp_inbound_messages_chat_jid", table_name="whatsapp_inbound_messages")
    op.drop_index("ix_whatsapp_inbound_messages_chat", table_name="whatsapp_inbound_messages")
    op.drop_index("ix_whatsapp_inbound_messages_channel_account_id", table_name="whatsapp_inbound_messages")
    op.drop_index("ix_whatsapp_inbound_messages_account_id", table_name="whatsapp_inbound_messages")
    op.drop_table("whatsapp_inbound_messages")
