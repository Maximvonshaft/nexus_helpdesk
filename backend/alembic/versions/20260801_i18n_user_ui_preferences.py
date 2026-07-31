"""Add independent operator UI locale preferences.

Revision ID: 20260801_i18n_ui_locale
Revises: 20260729_r15_tenant_scope
Create Date: 2026-08-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260801_i18n_ui_locale"
down_revision = "20260729_r15_tenant_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_ui_preferences",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "ui_locale",
            sa.String(length=16),
            nullable=False,
            server_default="zh-CN",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "ui_locale IN ('zh-CN','en','de')",
            name="ck_user_ui_preferences_locale",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("user_ui_preferences")
