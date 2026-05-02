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
    with op.batch_alter_table("webchat_conversations") as batch_op:
        batch_op.add_column(sa.Column("visitor_token_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index("ix_webchat_conversations_visitor_token_expires_at", ["visitor_token_expires_at"])

    with op.batch_alter_table("webchat_messages") as batch_op:
        batch_op.add_column(sa.Column("client_message_id", sa.String(length=120), nullable=True))
        batch_op.create_index("ix_webchat_messages_client_message_id", ["client_message_id"])
        batch_op.create_unique_constraint(
            "uq_webchat_message_client_id",
            ["conversation_id", "direction", "client_message_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("webchat_messages") as batch_op:
        batch_op.drop_constraint("uq_webchat_message_client_id", type_="unique")
        batch_op.drop_index("ix_webchat_messages_client_message_id")
        batch_op.drop_column("client_message_id")

    with op.batch_alter_table("webchat_conversations") as batch_op:
        batch_op.drop_index("ix_webchat_conversations_visitor_token_expires_at")
        batch_op.drop_column("visitor_token_expires_at")
