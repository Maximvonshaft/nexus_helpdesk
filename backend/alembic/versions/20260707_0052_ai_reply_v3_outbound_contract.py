"""ai reply v3 outbound contract payload

Revision ID: 20260707_0052
Revises: 20260707_0051
Create Date: 2026-07-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260707_0052"
down_revision = "20260707_0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ticket_outbound_messages", sa.Column("runtime_contract_payload_json", sa.Text(), nullable=True))
    op.add_column("ticket_outbound_messages", sa.Column("runtime_contract_payload_sha256", sa.String(length=64), nullable=True))
    op.add_column("ticket_outbound_messages", sa.Column("runtime_reply_type", sa.String(length=40), nullable=True))
    op.create_index("ix_ticket_outbound_messages_runtime_reply_type", "ticket_outbound_messages", ["runtime_reply_type"])

    op.add_column("tickets", sa.Column("last_ai_update", sa.Text(), nullable=True))
    op.add_column("tickets", sa.Column("last_runtime_reply_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_tickets_last_runtime_reply_at", "tickets", ["last_runtime_reply_at"])


def downgrade() -> None:
    op.drop_index("ix_tickets_last_runtime_reply_at", table_name="tickets")
    op.drop_column("tickets", "last_runtime_reply_at")
    op.drop_column("tickets", "last_ai_update")

    op.drop_index("ix_ticket_outbound_messages_runtime_reply_type", table_name="ticket_outbound_messages")
    op.drop_column("ticket_outbound_messages", "runtime_reply_type")
    op.drop_column("ticket_outbound_messages", "runtime_contract_payload_sha256")
    op.drop_column("ticket_outbound_messages", "runtime_contract_payload_json")
