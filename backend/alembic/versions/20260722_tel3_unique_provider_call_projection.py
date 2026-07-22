"""Enforce one canonical Voice Session per Provider call.

Revision ID: 20260722_tel3
Revises: 20260722_tel2
Create Date: 2026-07-22
"""

from __future__ import annotations

from alembic import op

revision = "20260722_tel3"
down_revision = "20260722_tel2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE webchat_voice_sessions
        SET provider_call_id = NULL
        WHERE id IN (
            SELECT id
            FROM (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY provider, provider_call_id
                        ORDER BY id ASC
                    ) AS duplicate_rank
                FROM webchat_voice_sessions
                WHERE provider_call_id IS NOT NULL
            ) AS ranked_provider_calls
            WHERE duplicate_rank > 1
        )
        """
    )
    op.create_index(
        "uq_webchat_voice_sessions_provider_call",
        "webchat_voice_sessions",
        ["provider", "provider_call_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_webchat_voice_sessions_provider_call",
        table_name="webchat_voice_sessions",
    )
