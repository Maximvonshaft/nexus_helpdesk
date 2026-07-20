"""add canonical user security state

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
        "user_security_states",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("session_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
        sa.CheckConstraint("session_version >= 1", name="ck_user_security_states_session_version"),
    )
    op.create_index(
        "ix_user_security_states_must_change_password",
        "user_security_states",
        ["must_change_password"],
    )
    op.create_index(
        "ix_user_security_states_last_login_at",
        "user_security_states",
        ["last_login_at"],
    )

    # Existing users retain their current credentials and sessions. New users are
    # forced to rotate their administrator-issued password by the ORM authority.
    op.execute(
        sa.text(
            """
            INSERT INTO user_security_states (
                user_id,
                session_version,
                must_change_password,
                password_changed_at,
                last_login_at,
                created_at,
                updated_at
            )
            SELECT id, 1, false, NULL, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            FROM users
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_user_security_states_last_login_at", table_name="user_security_states")
    op.drop_index("ix_user_security_states_must_change_password", table_name="user_security_states")
    op.drop_table("user_security_states")
