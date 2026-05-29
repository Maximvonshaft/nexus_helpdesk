"""outbound message attachments

Revision ID: 20260529_0039
Revises: 20260527_0038
Create Date: 2026-05-29
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260529_0039"
down_revision = "20260527_0038"
branch_labels = None
depends_on = None


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    if "ticket_outbound_attachments" in _tables(bind):
        return
    op.create_table(
        "ticket_outbound_attachments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("outbound_message_id", sa.Integer(), nullable=False),
        sa.Column("attachment_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["attachment_id"], ["ticket_attachments.id"]),
        sa.ForeignKeyConstraint(["outbound_message_id"], ["ticket_outbound_messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("outbound_message_id", "attachment_id", name="ux_ticket_outbound_attachment"),
    )
    op.create_index("ix_ticket_outbound_attachments_outbound_message_id", "ticket_outbound_attachments", ["outbound_message_id"], unique=False)
    op.create_index("ix_ticket_outbound_attachments_attachment_id", "ticket_outbound_attachments", ["attachment_id"], unique=False)
    op.create_index("ix_ticket_outbound_attachments_created_at", "ticket_outbound_attachments", ["created_at"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if "ticket_outbound_attachments" not in _tables(bind):
        return
    op.drop_index("ix_ticket_outbound_attachments_created_at", table_name="ticket_outbound_attachments")
    op.drop_index("ix_ticket_outbound_attachments_attachment_id", table_name="ticket_outbound_attachments")
    op.drop_index("ix_ticket_outbound_attachments_outbound_message_id", table_name="ticket_outbound_attachments")
    op.drop_table("ticket_outbound_attachments")
