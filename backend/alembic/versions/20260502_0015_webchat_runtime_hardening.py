"""harden webchat runtime fields

Revision ID: 20260502_0015
Revises: 20260502_0014
Create Date: 2026-05-02
"""

from alembic import op
import sqlalchemy as sa


revision = "20260502_0015"
down_revision = "20260502_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("webchat_conversations", sa.Column("visitor_token_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_webchat_conversations_visitor_token_expires_at", "webchat_conversations", ["visitor_token_expires_at"])

    op.add_column("webchat_messages", sa.Column("client_message_id", sa.String(length=120), nullable=True))
    op.create_index("ix_webchat_messages_client_message_id", "webchat_messages", ["client_message_id"])
    op.create_unique_constraint(
        "uq_webchat_message_client_id",
        "webchat_messages",
        ["conversation_id", "direction", "client_message_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_webchat_message_client_id", "webchat_messages", type_="unique")
    op.drop_index("ix_webchat_messages_client_message_id", table_name="webchat_messages")
    op.drop_column("webchat_messages", "client_message_id")
    op.drop_index("ix_webchat_conversations_visitor_token_expires_at", table_name="webchat_conversations")
    op.drop_column("webchat_conversations", "visitor_token_expires_at")
