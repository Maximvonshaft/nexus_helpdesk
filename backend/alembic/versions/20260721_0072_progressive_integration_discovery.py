"""add progressive Release-bound Integration discovery policy

Revision ID: 20260721_0072
Revises: 20260721_0071
Create Date: 2026-07-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260721_0072"
down_revision = "20260721_0071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM tool_execution_policies "
            "WHERE tool_name = 'integration.search' "
            "AND country_code = 'GLOBAL' AND channel = 'all'"
        )
    ).scalar_one()
    if int(existing or 0):
        raise RuntimeError("migration_0072_tool_policy_conflict:integration.search")
    bind.execute(
        sa.text(
            """
            INSERT INTO tool_execution_policies
                (tool_name, country_code, channel, enabled, ai_auto_executable,
                 risk_level, requires_tracking_number, requires_contact,
                 requires_customer_confirmation, requires_human_confirmation,
                 audit_level, created_at, updated_at)
            VALUES
                ('integration.search', 'GLOBAL', 'all', true, true, 'low',
                 false, false, false, false, 'standard',
                 CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM tool_execution_policies "
            "WHERE tool_name = 'integration.search' "
            "AND country_code = 'GLOBAL' AND channel = 'all'"
        )
    )
