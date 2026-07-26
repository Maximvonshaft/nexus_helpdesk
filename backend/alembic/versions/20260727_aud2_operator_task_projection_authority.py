"""Make OperatorTask a versioned, rebuildable projection.

Revision ID: 20260727_aud2
Revises: 20260727_aud1
Create Date: 2026-07-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260727_aud2"
down_revision = "20260727_aud1"
branch_labels = None
depends_on = None

_ACTIVE = "status NOT IN ('resolved', 'dropped', 'replayed', 'replay_failed', 'cancelled')"


def upgrade() -> None:
    op.add_column(
        "operator_tasks",
        sa.Column("source_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "operator_tasks",
        sa.Column(
            "projection_schema",
            sa.String(length=80),
            nullable=False,
            server_default="nexus.operator-task-projection.v1",
        ),
    )
    op.create_index(
        "ix_operator_tasks_projection_source_version",
        "operator_tasks",
        ["projection_schema", "source_version"],
        unique=False,
    )

    # The former webchat task used Conversation/Ticket fields as its source and
    # could issue reverse mutations. Retire only those active projections; the
    # current HandoffRequest authority will rebuild them with source_type
    # ``webchat_handoff`` and source_id=<handoff request id>.
    op.execute(
        sa.text(
            "UPDATE operator_tasks "
            "SET status = 'cancelled', "
            "    reason_code = 'retired_conversation_owned_handoff_projection', "
            "    resolved_at = COALESCE(resolved_at, CURRENT_TIMESTAMP), "
            "    updated_at = CURRENT_TIMESTAMP "
            "WHERE source_type = 'webchat' "
            "  AND task_type = 'handoff' "
            f"  AND {_ACTIVE}"
        )
    )


def downgrade() -> None:
    # Projection rows are disposable and are not resurrected on downgrade.
    op.drop_index(
        "ix_operator_tasks_projection_source_version",
        table_name="operator_tasks",
    )
    op.drop_column("operator_tasks", "projection_schema")
    op.drop_column("operator_tasks", "source_version")
