"""webcall ai worker claim lifecycle

Revision ID: 20260523_wcall_ai2
Revises: 20260523_wcall_ai1
Create Date: 2026-05-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260523_wcall_ai2"
down_revision = "20260523_wcall_ai1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("webchat_voice_sessions", sa.Column("ai_agent_worker_id", sa.String(length=120), nullable=True))
    op.add_column("webchat_voice_sessions", sa.Column("ai_agent_claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("webchat_voice_sessions", sa.Column("ai_agent_lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("webchat_voice_sessions", sa.Column("ai_agent_last_heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("webchat_voice_sessions", sa.Column("ai_agent_error_code", sa.String(length=120), nullable=True))
    op.add_column("webchat_voice_sessions", sa.Column("ai_agent_error_message", sa.Text(), nullable=True))
    op.create_index("ix_webchat_voice_sessions_ai_agent_worker_id", "webchat_voice_sessions", ["ai_agent_worker_id"], unique=False)
    op.create_index(
        "ix_webchat_voice_sessions_ai_agent_lease_expires_at",
        "webchat_voice_sessions",
        ["ai_agent_lease_expires_at"],
        unique=False,
    )
    op.create_index("ix_webchat_voice_sessions_ai_agent_error_code", "webchat_voice_sessions", ["ai_agent_error_code"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_webchat_voice_sessions_ai_agent_error_code", table_name="webchat_voice_sessions")
    op.drop_index("ix_webchat_voice_sessions_ai_agent_lease_expires_at", table_name="webchat_voice_sessions")
    op.drop_index("ix_webchat_voice_sessions_ai_agent_worker_id", table_name="webchat_voice_sessions")
    op.drop_column("webchat_voice_sessions", "ai_agent_error_message")
    op.drop_column("webchat_voice_sessions", "ai_agent_error_code")
    op.drop_column("webchat_voice_sessions", "ai_agent_last_heartbeat_at")
    op.drop_column("webchat_voice_sessions", "ai_agent_lease_expires_at")
    op.drop_column("webchat_voice_sessions", "ai_agent_claimed_at")
    op.drop_column("webchat_voice_sessions", "ai_agent_worker_id")
