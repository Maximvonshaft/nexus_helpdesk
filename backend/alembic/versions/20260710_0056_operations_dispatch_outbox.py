"""operations dispatch outbox

Revision ID: 20260710_0056
Revises: 20260709_0054
Create Date: 2026-07-10
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260710_0056"
down_revision = "20260709_0054"
branch_labels = None
depends_on = None


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    if "operations_dispatch_outbox" in _tables(bind):
        return

    op.create_table(
        "operations_dispatch_outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticket_id", sa.Integer(), nullable=True),
        sa.Column("dispatch_key", sa.String(length=80), nullable=False),
        sa.Column("tenant_key", sa.String(length=80), nullable=False, server_default="default"),
        sa.Column("country_code", sa.String(length=16), nullable=False),
        sa.Column("channel_key", sa.String(length=40), nullable=False),
        sa.Column("routing_rule_id", sa.Integer(), nullable=False),
        sa.Column("destination_group_key", sa.String(length=200), nullable=False),
        sa.Column("destination_group_hash", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=120), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_acknowledgement", sa.Text(), nullable=True),
        sa.Column("external_reference_safe", sa.String(length=160), nullable=True),
        sa.Column("error_category", sa.String(length=80), nullable=True),
        sa.Column("error_summary_redacted", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
        sa.ForeignKeyConstraint(["routing_rule_id"], ["whatsapp_routing_rules.id"]),
        sa.UniqueConstraint("dispatch_key", name="uq_operations_dispatch_outbox_dispatch_key"),
        sa.CheckConstraint(
            "status IN ('pending','processing','dispatched','retryable','failed','cancelled','dead_letter')",
            name="ck_operations_dispatch_outbox_status",
        ),
    )
    op.create_index("ix_operations_dispatch_outbox_ticket_id", "operations_dispatch_outbox", ["ticket_id"])
    op.create_index("ix_operations_dispatch_outbox_routing_rule_id", "operations_dispatch_outbox", ["routing_rule_id"])
    op.create_index("ix_operations_dispatch_outbox_status", "operations_dispatch_outbox", ["status"])
    op.create_index("ix_operations_dispatch_outbox_next_retry_at", "operations_dispatch_outbox", ["next_retry_at"])
    op.create_index("ix_operations_dispatch_outbox_lease_owner", "operations_dispatch_outbox", ["lease_owner"])
    op.create_index("ix_operations_dispatch_outbox_lease_expires_at", "operations_dispatch_outbox", ["lease_expires_at"])
    op.create_index("ix_operations_dispatch_outbox_error_category", "operations_dispatch_outbox", ["error_category"])
    op.create_index("ix_operations_dispatch_outbox_created_at", "operations_dispatch_outbox", ["created_at"])
    op.create_index("ix_operations_dispatch_outbox_updated_at", "operations_dispatch_outbox", ["updated_at"])
    op.create_index("ix_operations_dispatch_outbox_dispatched_at", "operations_dispatch_outbox", ["dispatched_at"])
    op.create_index("ix_operations_dispatch_outbox_cancelled_at", "operations_dispatch_outbox", ["cancelled_at"])
    op.create_index(
        "ix_operations_dispatch_outbox_scope",
        "operations_dispatch_outbox",
        ["tenant_key", "country_code", "channel_key"],
    )
    op.create_index(
        "ix_operations_dispatch_outbox_due",
        "operations_dispatch_outbox",
        ["status", "next_retry_at", "created_at"],
    )
    op.create_index(
        "ix_operations_dispatch_outbox_lease",
        "operations_dispatch_outbox",
        ["status", "lease_expires_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    if "operations_dispatch_outbox" in _tables(bind):
        op.drop_table("operations_dispatch_outbox")
