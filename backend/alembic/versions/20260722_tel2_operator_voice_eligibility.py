"""Canonical operator voice eligibility.

Revision ID: 20260722_tel2
Revises: 20260722_tel1
Create Date: 2026-07-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260722_tel2"
down_revision = "20260722_tel1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("operator_agent_states") as batch:
        batch.add_column(
            sa.Column(
                "voice_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.create_index(
            "ix_operator_agent_states_voice_eligibility",
            ["voice_enabled", "status", "last_heartbeat_at"],
        )


def downgrade() -> None:
    with op.batch_alter_table("operator_agent_states") as batch:
        batch.drop_index("ix_operator_agent_states_voice_eligibility")
        batch.drop_column("voice_enabled")
