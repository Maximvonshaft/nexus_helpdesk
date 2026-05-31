"""email inbound messages

Revision ID: 20260530_0043
Revises: 20260529_0042
Create Date: 2026-05-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260530_0043"
down_revision = "20260529_0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ticket_inbound_email_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id"), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("source", sa.String(length=40), nullable=False, server_default="manual_sync"),
        sa.Column("provider", sa.String(length=80), nullable=False, server_default="manual"),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("from_address", sa.String(length=320), nullable=False),
        sa.Column("from_name", sa.String(length=160), nullable=True),
        sa.Column("to_address", sa.String(length=320), nullable=True),
        sa.Column("cc", sa.Text(), nullable=True),
        sa.Column("subject", sa.String(length=255), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("body_preview", sa.Text(), nullable=True),
        sa.Column("mailbox_thread_id", sa.String(length=255), nullable=False),
        sa.Column("mailbox_message_id", sa.String(length=255), nullable=True),
        sa.Column("mailbox_references", sa.Text(), nullable=True),
        sa.Column("in_reply_to", sa.String(length=255), nullable=True),
        sa.Column("ticket_event_id", sa.Integer(), sa.ForeignKey("ticket_events.id"), nullable=True),
        sa.Column("audit_id", sa.Integer(), sa.ForeignKey("admin_audit_logs.id"), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_ticket_inbound_email_messages_ticket_id", "ticket_inbound_email_messages", ["ticket_id"], unique=False)
    op.create_index("ix_ticket_inbound_email_messages_actor_id", "ticket_inbound_email_messages", ["actor_id"], unique=False)
    op.create_index("ix_ticket_inbound_email_messages_source", "ticket_inbound_email_messages", ["source"], unique=False)
    op.create_index("ix_ticket_inbound_email_messages_provider", "ticket_inbound_email_messages", ["provider"], unique=False)
    op.create_index("ix_ticket_inbound_email_messages_provider_message_id", "ticket_inbound_email_messages", ["provider_message_id"], unique=False)
    op.create_index("ix_ticket_inbound_email_messages_from_address", "ticket_inbound_email_messages", ["from_address"], unique=False)
    op.create_index("ix_ticket_inbound_email_messages_mailbox_thread_id", "ticket_inbound_email_messages", ["mailbox_thread_id"], unique=False)
    op.create_index("ix_ticket_inbound_email_messages_mailbox_message_id", "ticket_inbound_email_messages", ["mailbox_message_id"], unique=False)
    op.create_index("ix_ticket_inbound_email_messages_in_reply_to", "ticket_inbound_email_messages", ["in_reply_to"], unique=False)
    op.create_index("ix_ticket_inbound_email_messages_ticket_event_id", "ticket_inbound_email_messages", ["ticket_event_id"], unique=False)
    op.create_index("ix_ticket_inbound_email_messages_audit_id", "ticket_inbound_email_messages", ["audit_id"], unique=False)
    op.create_index("ix_ticket_inbound_email_messages_received_at", "ticket_inbound_email_messages", ["received_at"], unique=False)
    op.create_index("ix_ticket_inbound_email_messages_created_at", "ticket_inbound_email_messages", ["created_at"], unique=False)
    op.create_index("ix_ticket_inbound_email_messages_ticket_received", "ticket_inbound_email_messages", ["ticket_id", "received_at"], unique=False)
    op.create_index(
        "ux_ticket_inbound_email_messages_ticket_mailbox_message",
        "ticket_inbound_email_messages",
        ["ticket_id", "mailbox_message_id"],
        unique=True,
        sqlite_where=sa.text("mailbox_message_id IS NOT NULL"),
        postgresql_where=sa.text("mailbox_message_id IS NOT NULL"),
    )
    op.create_index(
        "ux_ticket_inbound_email_messages_provider_message",
        "ticket_inbound_email_messages",
        ["provider", "provider_message_id"],
        unique=True,
        sqlite_where=sa.text("provider_message_id IS NOT NULL"),
        postgresql_where=sa.text("provider_message_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ux_ticket_inbound_email_messages_provider_message", table_name="ticket_inbound_email_messages")
    op.drop_index("ux_ticket_inbound_email_messages_ticket_mailbox_message", table_name="ticket_inbound_email_messages")
    op.drop_index("ix_ticket_inbound_email_messages_ticket_received", table_name="ticket_inbound_email_messages")
    op.drop_index("ix_ticket_inbound_email_messages_created_at", table_name="ticket_inbound_email_messages")
    op.drop_index("ix_ticket_inbound_email_messages_received_at", table_name="ticket_inbound_email_messages")
    op.drop_index("ix_ticket_inbound_email_messages_audit_id", table_name="ticket_inbound_email_messages")
    op.drop_index("ix_ticket_inbound_email_messages_ticket_event_id", table_name="ticket_inbound_email_messages")
    op.drop_index("ix_ticket_inbound_email_messages_in_reply_to", table_name="ticket_inbound_email_messages")
    op.drop_index("ix_ticket_inbound_email_messages_mailbox_message_id", table_name="ticket_inbound_email_messages")
    op.drop_index("ix_ticket_inbound_email_messages_mailbox_thread_id", table_name="ticket_inbound_email_messages")
    op.drop_index("ix_ticket_inbound_email_messages_from_address", table_name="ticket_inbound_email_messages")
    op.drop_index("ix_ticket_inbound_email_messages_provider_message_id", table_name="ticket_inbound_email_messages")
    op.drop_index("ix_ticket_inbound_email_messages_provider", table_name="ticket_inbound_email_messages")
    op.drop_index("ix_ticket_inbound_email_messages_source", table_name="ticket_inbound_email_messages")
    op.drop_index("ix_ticket_inbound_email_messages_actor_id", table_name="ticket_inbound_email_messages")
    op.drop_index("ix_ticket_inbound_email_messages_ticket_id", table_name="ticket_inbound_email_messages")
    op.drop_table("ticket_inbound_email_messages")
