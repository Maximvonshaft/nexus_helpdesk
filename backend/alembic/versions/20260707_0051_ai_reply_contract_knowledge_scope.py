"""ai reply contract and knowledge scope hardening

Revision ID: 20260707_0051
Revises: 20260706_0050
Create Date: 2026-07-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260707_0051"
down_revision = "20260706_0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ticket_outbound_messages", sa.Column("origin", sa.String(length=80), nullable=True))
    op.add_column("ticket_outbound_messages", sa.Column("runtime_trace_id", sa.String(length=120), nullable=True))
    op.add_column("ticket_outbound_messages", sa.Column("runtime_contract_version", sa.String(length=80), nullable=True))
    op.add_column("ticket_outbound_messages", sa.Column("runtime_signature", sa.String(length=128), nullable=True))
    op.add_column("ticket_outbound_messages", sa.Column("safety_status", sa.String(length=40), nullable=True))
    op.create_index("ix_ticket_outbound_messages_origin", "ticket_outbound_messages", ["origin"])
    op.create_index("ix_ticket_outbound_messages_runtime_trace_id", "ticket_outbound_messages", ["runtime_trace_id"])
    op.create_index("ix_ticket_outbound_messages_runtime_contract_version", "ticket_outbound_messages", ["runtime_contract_version"])
    op.create_index("ix_ticket_outbound_messages_safety_status", "ticket_outbound_messages", ["safety_status"])

    for table in ("knowledge_items", "knowledge_chunks"):
        op.add_column(table, sa.Column("tenant_id", sa.String(length=80), nullable=False, server_default="default"))
        op.add_column(table, sa.Column("brand_id", sa.String(length=80), nullable=False, server_default="default"))
        op.add_column(table, sa.Column("country_scope", sa.String(length=16), nullable=False, server_default="GLOBAL"))
        op.add_column(table, sa.Column("channel_scope", sa.String(length=40), nullable=False, server_default="all"))
        op.add_column(table, sa.Column("locale", sa.String(length=16), nullable=True))
        op.add_column(table, sa.Column("visibility", sa.String(length=40), nullable=False, server_default="customer"))
        op.add_column(table, sa.Column("shareability", sa.String(length=40), nullable=False, server_default="customer_visible"))
        op.add_column(table, sa.Column("authority_level", sa.String(length=40), nullable=False, server_default="faq"))
        op.add_column(table, sa.Column("risk_level", sa.String(length=40), nullable=False, server_default="low"))
        op.add_column(table, sa.Column("review_due_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column(table, sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True))
        op.add_column(table, sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True))
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])
        op.create_index(f"ix_{table}_brand_id", table, ["brand_id"])
        op.create_index(f"ix_{table}_country_scope", table, ["country_scope"])
        op.create_index(f"ix_{table}_channel_scope", table, ["channel_scope"])
        op.create_index(f"ix_{table}_locale", table, ["locale"])
        op.create_index(f"ix_{table}_visibility", table, ["visibility"])
        op.create_index(f"ix_{table}_shareability", table, ["shareability"])
        op.create_index(f"ix_{table}_authority_level", table, ["authority_level"])
        op.create_index(f"ix_{table}_risk_level", table, ["risk_level"])
        op.create_index(f"ix_{table}_review_due_at", table, ["review_due_at"])
        op.create_index(f"ix_{table}_valid_from", table, ["valid_from"])
        op.create_index(f"ix_{table}_valid_until", table, ["valid_until"])


def downgrade() -> None:
    for table in ("knowledge_chunks", "knowledge_items"):
        for column in (
            "valid_until",
            "valid_from",
            "review_due_at",
            "risk_level",
            "authority_level",
            "shareability",
            "visibility",
            "locale",
            "channel_scope",
            "country_scope",
            "brand_id",
            "tenant_id",
        ):
            op.drop_index(f"ix_{table}_{column}", table_name=table)
            op.drop_column(table, column)

    for column in ("safety_status", "runtime_contract_version", "runtime_trace_id", "origin"):
        op.drop_index(f"ix_ticket_outbound_messages_{column}", table_name="ticket_outbound_messages")
    op.drop_column("ticket_outbound_messages", "safety_status")
    op.drop_column("ticket_outbound_messages", "runtime_signature")
    op.drop_column("ticket_outbound_messages", "runtime_contract_version")
    op.drop_column("ticket_outbound_messages", "runtime_trace_id")
    op.drop_column("ticket_outbound_messages", "origin")
