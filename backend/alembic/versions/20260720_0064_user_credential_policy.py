"""add canonical user credential policy state

Revision ID: 20260720_0064
Revises: 20260720_0063
Create Date: 2026-07-20
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260720_0064"
down_revision = "20260720_0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_credential_policies",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index(
        "ix_user_credential_policies_must_change_password",
        "user_credential_policies",
        ["must_change_password"],
        unique=False,
    )
    op.create_index(
        "ix_user_credential_policies_last_login_at",
        "user_credential_policies",
        ["last_login_at"],
        unique=False,
    )
    op.execute(
        sa.text(
            """
            INSERT INTO user_credential_policies
                (user_id, must_change_password, password_changed_at, last_login_at, created_at, updated_at)
            SELECT id, false, NULL, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            FROM users
            """
        )
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_credential_policies_last_login_at",
        table_name="user_credential_policies",
    )
    op.drop_index(
        "ix_user_credential_policies_must_change_password",
        table_name="user_credential_policies",
    )
    op.drop_table("user_credential_policies")
