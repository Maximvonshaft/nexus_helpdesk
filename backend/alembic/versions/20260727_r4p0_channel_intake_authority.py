"""Add canonical Tenant-scoped channel intake authorities.

Revision ID: 20260727_r4p0
Revises: 20260727_aud9
Create Date: 2026-07-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260727_r4p0"
down_revision = "20260727_aud9"
branch_labels = None
depends_on = None


def _assert_no_identity_collisions(bind) -> None:
    checks = {
        "email": "lower(trim(coalesce(email_normalized, email)))",
        "phone": "trim(coalesce(phone_normalized, phone))",
        "external_ref": "lower(trim(external_ref))",
    }
    for identity_type, expression in checks.items():
        collision_count = int(
            bind.execute(
                sa.text(
                    "SELECT count(*) FROM ("
                    " SELECT tenant_id, "
                    + expression
                    + " AS identity_value, count(*) AS c"
                    " FROM customers"
                    " WHERE tenant_id IS NOT NULL AND "
                    + expression
                    + " <> ''"
                    " GROUP BY tenant_id, "
                    + expression
                    + " HAVING count(*) > 1"
                    ") collisions"
                )
            ).scalar()
            or 0
        )
        if collision_count:
            raise RuntimeError(
                "customer_identity_collision_requires_explicit_remediation:"
                f"{identity_type}:{collision_count}"
            )


def upgrade() -> None:
    bind = op.get_bind()
    _assert_no_identity_collisions(bind)

    op.create_table(
        "customer_identity_bindings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("identity_type", sa.String(length=32), nullable=False),
        sa.Column("normalized_value", sa.String(length=320), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "identity_type IN ('email','phone','external_ref')",
            name="ck_customer_identity_type",
        ),
        sa.CheckConstraint(
            "length(trim(normalized_value)) > 0",
            name="ck_customer_identity_value_nonempty",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "identity_type",
            "normalized_value",
            name="uq_customer_identity_tenant_type_value",
        ),
    )
    op.create_index(
        "ix_customer_identity_bindings_tenant_id",
        "customer_identity_bindings",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_customer_identity_bindings_customer_id",
        "customer_identity_bindings",
        ["customer_id"],
        unique=False,
    )
    op.create_index(
        "ix_customer_identity_bindings_identity_type",
        "customer_identity_bindings",
        ["identity_type"],
        unique=False,
    )
    op.create_index(
        "ix_customer_identity_customer",
        "customer_identity_bindings",
        ["tenant_id", "customer_id", "identity_type"],
        unique=False,
    )

    bind.execute(
        sa.text(
            "INSERT INTO customer_identity_bindings "
            "(tenant_id, customer_id, identity_type, normalized_value, source, created_at, updated_at) "
            "SELECT tenant_id, id, 'email', lower(trim(coalesce(email_normalized, email))), "
            "'migration_backfill', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
            "FROM customers WHERE tenant_id IS NOT NULL "
            "AND lower(trim(coalesce(email_normalized, email))) <> ''"
        )
    )
    bind.execute(
        sa.text(
            "INSERT INTO customer_identity_bindings "
            "(tenant_id, customer_id, identity_type, normalized_value, source, created_at, updated_at) "
            "SELECT tenant_id, id, 'phone', trim(coalesce(phone_normalized, phone)), "
            "'migration_backfill', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
            "FROM customers WHERE tenant_id IS NOT NULL "
            "AND trim(coalesce(phone_normalized, phone)) <> ''"
        )
    )
    bind.execute(
        sa.text(
            "INSERT INTO customer_identity_bindings "
            "(tenant_id, customer_id, identity_type, normalized_value, source, created_at, updated_at) "
            "SELECT tenant_id, id, 'external_ref', lower(trim(external_ref)), "
            "'migration_backfill', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
            "FROM customers WHERE tenant_id IS NOT NULL "
            "AND lower(trim(external_ref)) <> ''"
        )
    )

    op.create_table(
        "email_intake_quarantine",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("provider_message_id", sa.String(length=255), nullable=False),
        sa.Column("mailbox_uid", sa.String(length=80), nullable=False),
        sa.Column("from_address", sa.String(length=320), nullable=False),
        sa.Column("from_name", sa.String(length=160), nullable=True),
        sa.Column("to_address", sa.String(length=320), nullable=True),
        sa.Column("cc", sa.Text(), nullable=True),
        sa.Column("subject", sa.String(length=255), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("mailbox_message_id", sa.String(length=500), nullable=True),
        sa.Column("mailbox_references", sa.Text(), nullable=True),
        sa.Column("in_reply_to", sa.String(length=500), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=80), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("ticket_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending_intake','projected','rejected')",
            name="ck_email_intake_quarantine_status",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["outbound_email_accounts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["tickets.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_id",
            "provider_message_id",
            name="uq_email_intake_quarantine_account_provider_message",
        ),
    )
    op.create_index(
        "ix_email_intake_quarantine_tenant_id",
        "email_intake_quarantine",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_email_intake_quarantine_account_id",
        "email_intake_quarantine",
        ["account_id"],
        unique=False,
    )
    op.create_index(
        "ix_email_intake_quarantine_provider_message_id",
        "email_intake_quarantine",
        ["provider_message_id"],
        unique=False,
    )
    op.create_index(
        "ix_email_intake_quarantine_from_address",
        "email_intake_quarantine",
        ["from_address"],
        unique=False,
    )
    op.create_index(
        "ix_email_intake_quarantine_status",
        "email_intake_quarantine",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_email_intake_quarantine_conversation_id",
        "email_intake_quarantine",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        "ix_email_intake_quarantine_ticket_id",
        "email_intake_quarantine",
        ["ticket_id"],
        unique=False,
    )
    op.create_index(
        "ix_email_intake_quarantine_created_at",
        "email_intake_quarantine",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_email_intake_quarantine_tenant_status",
        "email_intake_quarantine",
        ["tenant_id", "status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_email_intake_quarantine_tenant_status",
        table_name="email_intake_quarantine",
    )
    op.drop_index(
        "ix_email_intake_quarantine_created_at",
        table_name="email_intake_quarantine",
    )
    op.drop_index(
        "ix_email_intake_quarantine_ticket_id",
        table_name="email_intake_quarantine",
    )
    op.drop_index(
        "ix_email_intake_quarantine_conversation_id",
        table_name="email_intake_quarantine",
    )
    op.drop_index(
        "ix_email_intake_quarantine_status",
        table_name="email_intake_quarantine",
    )
    op.drop_index(
        "ix_email_intake_quarantine_from_address",
        table_name="email_intake_quarantine",
    )
    op.drop_index(
        "ix_email_intake_quarantine_provider_message_id",
        table_name="email_intake_quarantine",
    )
    op.drop_index(
        "ix_email_intake_quarantine_account_id",
        table_name="email_intake_quarantine",
    )
    op.drop_index(
        "ix_email_intake_quarantine_tenant_id",
        table_name="email_intake_quarantine",
    )
    op.drop_table("email_intake_quarantine")

    op.drop_index(
        "ix_customer_identity_customer",
        table_name="customer_identity_bindings",
    )
    op.drop_index(
        "ix_customer_identity_bindings_identity_type",
        table_name="customer_identity_bindings",
    )
    op.drop_index(
        "ix_customer_identity_bindings_customer_id",
        table_name="customer_identity_bindings",
    )
    op.drop_index(
        "ix_customer_identity_bindings_tenant_id",
        table_name="customer_identity_bindings",
    )
    op.drop_table("customer_identity_bindings")
