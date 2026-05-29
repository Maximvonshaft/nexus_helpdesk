"""email mailbox identity fields

Revision ID: 20260529_0040
Revises: 20260529_0039
Create Date: 2026-05-29
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260529_0040"
down_revision = "20260529_0039"
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


def _create_index_once(bind, name: str, columns: list[str]) -> None:
    if name not in _indexes(bind, "ticket_outbound_messages"):
        op.create_index(name, "ticket_outbound_messages", columns, unique=False)


def upgrade() -> None:
    bind = op.get_bind()
    if "ticket_outbound_messages" not in _tables(bind):
        return

    columns = _columns(bind, "ticket_outbound_messages")
    if "mailbox_thread_id" not in columns:
        op.add_column("ticket_outbound_messages", sa.Column("mailbox_thread_id", sa.String(length=255), nullable=True))
    if "mailbox_message_id" not in columns:
        op.add_column("ticket_outbound_messages", sa.Column("mailbox_message_id", sa.String(length=255), nullable=True))
    if "mailbox_references" not in columns:
        op.add_column("ticket_outbound_messages", sa.Column("mailbox_references", sa.Text(), nullable=True))

    _create_index_once(bind, "ix_ticket_outbound_messages_mailbox_thread_id", ["mailbox_thread_id"])
    _create_index_once(bind, "ix_ticket_outbound_messages_mailbox_message_id", ["mailbox_message_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if "ticket_outbound_messages" not in _tables(bind):
        return

    indexes = _indexes(bind, "ticket_outbound_messages")
    if "ix_ticket_outbound_messages_mailbox_message_id" in indexes:
        op.drop_index("ix_ticket_outbound_messages_mailbox_message_id", table_name="ticket_outbound_messages")
    if "ix_ticket_outbound_messages_mailbox_thread_id" in indexes:
        op.drop_index("ix_ticket_outbound_messages_mailbox_thread_id", table_name="ticket_outbound_messages")

    columns = _columns(bind, "ticket_outbound_messages")
    for column in ("mailbox_references", "mailbox_message_id", "mailbox_thread_id"):
        if column in columns:
            op.drop_column("ticket_outbound_messages", column)
