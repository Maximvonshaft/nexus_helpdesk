"""Add executable data-processing restrictions.

Revision ID: 20260727_aud7
Revises: 20260727_aud6
Create Date: 2026-07-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260727_aud7"
down_revision = "20260727_aud6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_processing_restrictions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("blocked_purposes_json", sa.JSON(), nullable=False),
        sa.Column("allowed_purposes_json", sa.JSON(), nullable=False),
        sa.Column("reason_code", sa.String(length=120), nullable=False),
        sa.Column("placed_by", sa.Integer(), nullable=True),
        sa.Column("released_by", sa.Integer(), nullable=True),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('active','released')",
            name="ck_data_processing_restriction_status",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["placed_by"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["released_by"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["data_subject_requests.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "request_id",
            name="uq_data_processing_restriction_request",
        ),
    )
    op.create_index(
        "ix_data_processing_restrictions_tenant_id",
        "data_processing_restrictions",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_data_processing_restrictions_customer_id",
        "data_processing_restrictions",
        ["customer_id"],
        unique=False,
    )
    op.create_index(
        "ix_data_processing_restrictions_request_id",
        "data_processing_restrictions",
        ["request_id"],
        unique=False,
    )
    op.create_index(
        "ix_data_processing_restrictions_status",
        "data_processing_restrictions",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_data_processing_restriction_customer_status",
        "data_processing_restrictions",
        ["tenant_id", "customer_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_data_processing_restrictions_placed_by",
        "data_processing_restrictions",
        ["placed_by"],
        unique=False,
    )
    op.create_index(
        "ix_data_processing_restrictions_released_by",
        "data_processing_restrictions",
        ["released_by"],
        unique=False,
    )
    op.create_index(
        "ix_data_processing_restrictions_placed_at",
        "data_processing_restrictions",
        ["placed_at"],
        unique=False,
    )
    op.create_index(
        "ix_data_processing_restrictions_released_at",
        "data_processing_restrictions",
        ["released_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_data_processing_restrictions_released_at",
        table_name="data_processing_restrictions",
    )
    op.drop_index(
        "ix_data_processing_restrictions_placed_at",
        table_name="data_processing_restrictions",
    )
    op.drop_index(
        "ix_data_processing_restrictions_released_by",
        table_name="data_processing_restrictions",
    )
    op.drop_index(
        "ix_data_processing_restrictions_placed_by",
        table_name="data_processing_restrictions",
    )
    op.drop_index(
        "ix_data_processing_restriction_customer_status",
        table_name="data_processing_restrictions",
    )
    op.drop_index(
        "ix_data_processing_restrictions_status",
        table_name="data_processing_restrictions",
    )
    op.drop_index(
        "ix_data_processing_restrictions_request_id",
        table_name="data_processing_restrictions",
    )
    op.drop_index(
        "ix_data_processing_restrictions_customer_id",
        table_name="data_processing_restrictions",
    )
    op.drop_index(
        "ix_data_processing_restrictions_tenant_id",
        table_name="data_processing_restrictions",
    )
    op.drop_table("data_processing_restrictions")
