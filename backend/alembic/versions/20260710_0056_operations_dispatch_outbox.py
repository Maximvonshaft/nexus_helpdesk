"""add durable operations dispatch outbox

Revision ID: 20260710_0056
Revises: 20260710_0055
Create Date: 2026-07-10

The table stores only safe routing identifiers, hashes, lease/retry state and
redacted provider outcome metadata. It contains no customer-visible message
body, raw provider destination, tracking number, phone, email, address or
credential.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260710_0056"
down_revision = "20260710_0055"
branch_labels = None
depends_on = None

_TABLE = "operations_dispatch_outbox"
_INDEXES = (
    "ix_operations_dispatch_outbox_ticket_id",
    "ix_operations_dispatch_outbox_routing_rule_id",
    "ix_operations_dispatch_outbox_status",
    "ix_operations_dispatch_outbox_next_retry_at",
    "ix_operations_dispatch_outbox_lease_owner",
    "ix_operations_dispatch_outbox_lease_expires_at",
    "ix_operations_dispatch_outbox_error_category",
    "ix_operations_dispatch_outbox_created_at",
    "ix_operations_dispatch_outbox_updated_at",
    "ix_operations_dispatch_outbox_dispatched_at",
    "ix_operations_dispatch_outbox_cancelled_at",
    "ix_operations_dispatch_outbox_scope",
    "ix_operations_dispatch_outbox_due",
    "ix_operations_dispatch_outbox_lease",
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE in inspector.get_table_names():
        return

    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
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
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','processing','dispatched','retryable','failed','cancelled','dead_letter')",
            name="ck_operations_dispatch_outbox_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_operations_dispatch_outbox_attempt_count_nonnegative"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_operations_dispatch_outbox_max_attempts_positive"),
        sa.CheckConstraint("attempt_count <= max_attempts", name="ck_operations_dispatch_outbox_attempt_count_bounded"),
        sa.CheckConstraint(
            "((status = 'processing' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL) "
            "OR (status <> 'processing' AND lease_owner IS NULL AND lease_expires_at IS NULL))",
            name="ck_operations_dispatch_outbox_lease_state",
        ),
        sa.CheckConstraint("(status <> 'retryable' OR next_retry_at IS NOT NULL)", name="ck_operations_dispatch_outbox_retry_timestamp"),
        sa.CheckConstraint("(status <> 'dispatched' OR dispatched_at IS NOT NULL)", name="ck_operations_dispatch_outbox_dispatched_timestamp"),
        sa.CheckConstraint("(status <> 'cancelled' OR cancelled_at IS NOT NULL)", name="ck_operations_dispatch_outbox_cancelled_timestamp"),
        sa.ForeignKeyConstraint(["routing_rule_id"], ["whatsapp_routing_rules.id"]),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dispatch_key", name="uq_operations_dispatch_outbox_dispatch_key"),
    )

    for name, columns in (
        ("ix_operations_dispatch_outbox_ticket_id", ["ticket_id"]),
        ("ix_operations_dispatch_outbox_routing_rule_id", ["routing_rule_id"]),
        ("ix_operations_dispatch_outbox_status", ["status"]),
        ("ix_operations_dispatch_outbox_next_retry_at", ["next_retry_at"]),
        ("ix_operations_dispatch_outbox_lease_owner", ["lease_owner"]),
        ("ix_operations_dispatch_outbox_lease_expires_at", ["lease_expires_at"]),
        ("ix_operations_dispatch_outbox_error_category", ["error_category"]),
        ("ix_operations_dispatch_outbox_created_at", ["created_at"]),
        ("ix_operations_dispatch_outbox_updated_at", ["updated_at"]),
        ("ix_operations_dispatch_outbox_dispatched_at", ["dispatched_at"]),
        ("ix_operations_dispatch_outbox_cancelled_at", ["cancelled_at"]),
        ("ix_operations_dispatch_outbox_scope", ["tenant_key", "country_code", "channel_key"]),
        ("ix_operations_dispatch_outbox_due", ["status", "next_retry_at", "created_at"]),
        ("ix_operations_dispatch_outbox_lease", ["status", "lease_expires_at"]),
    ):
        op.create_index(name, _TABLE, columns, unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in inspector.get_table_names():
        return
    existing = {item["name"] for item in inspector.get_indexes(_TABLE)}
    for name in reversed(_INDEXES):
        if name in existing:
            op.drop_index(name, table_name=_TABLE)
    op.drop_table(_TABLE)
