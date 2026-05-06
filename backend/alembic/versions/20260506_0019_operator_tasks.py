"""operator queue tasks

Revision ID: 20260506_0019
Revises: 20260506_0018
Create Date: 2026-05-06
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260506_0019"
down_revision = "20260506_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operator_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_type", sa.String(length=40), nullable=False),
        sa.Column("source_id", sa.String(length=160), nullable=True),
        sa.Column("ticket_id", sa.Integer(), nullable=True),
        sa.Column("webchat_conversation_id", sa.Integer(), nullable=True),
        sa.Column("unresolved_event_id", sa.Integer(), nullable=True),
        sa.Column("task_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="pending"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("assignee_id", sa.Integer(), nullable=True),
        sa.Column("reason_code", sa.String(length=160), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_operator_tasks_status_priority_created", "operator_tasks", ["status", "priority", "created_at"])
    op.create_index("ix_operator_tasks_ticket_id", "operator_tasks", ["ticket_id"])
    op.create_index("ix_operator_tasks_unresolved_event_id", "operator_tasks", ["unresolved_event_id"])


def downgrade() -> None:
    op.drop_index("ix_operator_tasks_unresolved_event_id", table_name="operator_tasks")
    op.drop_index("ix_operator_tasks_ticket_id", table_name="operator_tasks")
    op.drop_index("ix_operator_tasks_status_priority_created", table_name="operator_tasks")
    op.drop_table("operator_tasks")
