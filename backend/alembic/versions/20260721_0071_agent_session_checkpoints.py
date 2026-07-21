"""add expiring Agent Session checkpoints and specialist policy

Revision ID: 20260721_0071
Revises: 20260721_0070
Create Date: 2026-07-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260721_0071"
down_revision = "20260721_0070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_session_checkpoints",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_key", sa.String(length=80), nullable=False),
        sa.Column("session_id", sa.String(length=160), nullable=False),
        sa.Column(
            "release_id",
            sa.Integer(),
            sa.ForeignKey("agent_releases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_run_id",
            sa.Integer(),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("summary_sha256", sa.String(length=64), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("estimated_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "tenant_key",
            "session_id",
            "version",
            name="uq_agent_session_checkpoint_version",
        ),
        sa.CheckConstraint(
            "version > 0", name="ck_agent_session_checkpoint_version"
        ),
        sa.CheckConstraint(
            "estimated_tokens >= 0",
            name="ck_agent_session_checkpoint_tokens_nonnegative",
        ),
    )
    for column in (
        "tenant_key",
        "session_id",
        "release_id",
        "source_run_id",
        "version",
        "summary_sha256",
        "is_active",
        "created_at",
        "expires_at",
        "deactivated_at",
    ):
        op.create_index(
            f"ix_agent_session_checkpoints_{column}",
            "agent_session_checkpoints",
            [column],
        )
    op.create_index(
        "ix_agent_session_checkpoints_active",
        "agent_session_checkpoints",
        ["tenant_key", "session_id", "is_active", "created_at"],
    )

    bind = op.get_bind()
    existing = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM tool_execution_policies "
            "WHERE tool_name = 'specialist.delegate' "
            "AND country_code = 'GLOBAL' AND channel = 'all'"
        )
    ).scalar_one()
    if int(existing or 0):
        raise RuntimeError("migration_0071_tool_policy_conflict:specialist.delegate")
    bind.execute(
        sa.text(
            """
            INSERT INTO tool_execution_policies
                (tool_name, country_code, channel, enabled, ai_auto_executable,
                 risk_level, requires_tracking_number, requires_contact,
                 requires_customer_confirmation, requires_human_confirmation,
                 audit_level, created_at, updated_at)
            VALUES
                ('specialist.delegate', 'GLOBAL', 'all', true, true, 'medium',
                 false, false, false, false, 'detailed',
                 CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM tool_execution_policies "
            "WHERE tool_name = 'specialist.delegate' "
            "AND country_code = 'GLOBAL' AND channel = 'all'"
        )
    )
    op.drop_table("agent_session_checkpoints")
