"""email delivery receipts

Revision ID: 20260530_0044
Revises: 20260530_0043
Create Date: 2026-05-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260530_0044"
down_revision = "20260530_0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ticket_outbound_messages", sa.Column("delivery_status", sa.String(length=40), nullable=True))
    op.add_column("ticket_outbound_messages", sa.Column("delivery_event_type", sa.String(length=80), nullable=True))
    op.add_column("ticket_outbound_messages", sa.Column("delivery_receipt_provider", sa.String(length=80), nullable=True))
    op.add_column("ticket_outbound_messages", sa.Column("delivery_receipt_id", sa.String(length=255), nullable=True))
    op.add_column("ticket_outbound_messages", sa.Column("delivery_receipt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ticket_outbound_messages", sa.Column("delivery_detail", sa.Text(), nullable=True))
    op.add_column("ticket_outbound_messages", sa.Column("delivery_payload_json", sa.Text(), nullable=True))
    op.create_index("ix_ticket_outbound_messages_delivery_status", "ticket_outbound_messages", ["delivery_status"], unique=False)
    op.create_index("ix_ticket_outbound_messages_delivery_event_type", "ticket_outbound_messages", ["delivery_event_type"], unique=False)
    op.create_index("ix_ticket_outbound_messages_delivery_receipt_provider", "ticket_outbound_messages", ["delivery_receipt_provider"], unique=False)
    op.create_index("ix_ticket_outbound_messages_delivery_receipt_id", "ticket_outbound_messages", ["delivery_receipt_id"], unique=False)
    op.create_index("ix_ticket_outbound_messages_delivery_receipt_at", "ticket_outbound_messages", ["delivery_receipt_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_ticket_outbound_messages_delivery_receipt_at", table_name="ticket_outbound_messages")
    op.drop_index("ix_ticket_outbound_messages_delivery_receipt_id", table_name="ticket_outbound_messages")
    op.drop_index("ix_ticket_outbound_messages_delivery_receipt_provider", table_name="ticket_outbound_messages")
    op.drop_index("ix_ticket_outbound_messages_delivery_event_type", table_name="ticket_outbound_messages")
    op.drop_index("ix_ticket_outbound_messages_delivery_status", table_name="ticket_outbound_messages")
    op.drop_column("ticket_outbound_messages", "delivery_payload_json")
    op.drop_column("ticket_outbound_messages", "delivery_detail")
    op.drop_column("ticket_outbound_messages", "delivery_receipt_at")
    op.drop_column("ticket_outbound_messages", "delivery_receipt_id")
    op.drop_column("ticket_outbound_messages", "delivery_receipt_provider")
    op.drop_column("ticket_outbound_messages", "delivery_event_type")
    op.drop_column("ticket_outbound_messages", "delivery_status")
