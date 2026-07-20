"""add canonical user MFA credential policy

Revision ID: 20260720_0065
Revises: 20260720_0064
Create Date: 2026-07-20
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260720_0065"
down_revision = "20260720_0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_credential_policies",
        sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "user_credential_policies",
        sa.Column("mfa_secret_encrypted", sa.Text(), nullable=True),
    )
    op.add_column(
        "user_credential_policies",
        sa.Column("mfa_pending_secret_encrypted", sa.Text(), nullable=True),
    )
    op.add_column(
        "user_credential_policies",
        sa.Column("mfa_recovery_codes_json", sa.Text(), nullable=True),
    )
    op.add_column(
        "user_credential_policies",
        sa.Column("mfa_confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "user_credential_policies",
        sa.Column("mfa_last_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "user_credential_policies",
        sa.Column("mfa_last_used_step", sa.BigInteger(), nullable=True),
    )
    op.create_index(
        "ix_user_credential_policies_mfa_enabled",
        "user_credential_policies",
        ["mfa_enabled"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_credential_policies_mfa_enabled",
        table_name="user_credential_policies",
    )
    op.drop_column("user_credential_policies", "mfa_last_used_step")
    op.drop_column("user_credential_policies", "mfa_last_verified_at")
    op.drop_column("user_credential_policies", "mfa_confirmed_at")
    op.drop_column("user_credential_policies", "mfa_recovery_codes_json")
    op.drop_column("user_credential_policies", "mfa_pending_secret_encrypted")
    op.drop_column("user_credential_policies", "mfa_secret_encrypted")
    op.drop_column("user_credential_policies", "mfa_enabled")
