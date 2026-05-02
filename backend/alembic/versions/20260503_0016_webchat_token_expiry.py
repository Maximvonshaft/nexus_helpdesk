"""add webchat visitor token expiry

Revision ID: 20260503_0016
Revises: 20260502_wc_cards
Create Date: 2026-05-03
"""

from alembic import op
import sqlalchemy as sa

revision = "20260503_0016"
down_revision = "20260502_wc_cards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("webchat_conversations") as batch_op:
        batch_op.add_column(sa.Column("visitor_token_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index("ix_webchat_conversations_visitor_token_expires_at", ["visitor_token_expires_at"])


def downgrade() -> None:
    with op.batch_alter_table("webchat_conversations") as batch_op:
        batch_op.drop_index("ix_webchat_conversations_visitor_token_expires_at")
        batch_op.drop_column("visitor_token_expires_at")
