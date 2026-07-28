"""Converge active Handoff tasks on the source aggregate identity.

Revision ID: 20260728_r5_handoff
Revises: 20260727_wa4
Create Date: 2026-07-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260728_r5_handoff"
down_revision = "20260727_wa4"
branch_labels = None
depends_on = None

_TERMINAL = ("resolved", "dropped", "replayed", "replay_failed", "cancelled")
_OPEN_HANDOFF = ("requested", "accepted")


def _tables():
    tasks = sa.table(
        "operator_tasks",
        sa.column("id", sa.Integer),
        sa.column("source_type", sa.String),
        sa.column("source_id", sa.String),
        sa.column("source_version", sa.Integer),
        sa.column("projection_schema", sa.String),
        sa.column("ticket_id", sa.Integer),
        sa.column("webchat_conversation_id", sa.Integer),
        sa.column("task_type", sa.String),
        sa.column("status", sa.String),
        sa.column("priority", sa.Integer),
        sa.column("reason_code", sa.String),
    )
    requests = sa.table(
        "webchat_handoff_requests",
        sa.column("id", sa.Integer),
        sa.column("conversation_id", sa.Integer),
        sa.column("ticket_id", sa.Integer),
        sa.column("status", sa.String),
        sa.column("reason_code", sa.String),
        sa.column("lock_version", sa.Integer),
    )
    return tasks, requests


def upgrade() -> None:
    bind = op.get_bind()
    tasks, requests = _tables()
    active_tasks = bind.execute(
        sa.select(
            tasks.c.id,
            tasks.c.webchat_conversation_id,
            tasks.c.ticket_id,
            tasks.c.reason_code,
        ).where(
            tasks.c.task_type == "handoff",
            tasks.c.webchat_conversation_id.is_not(None),
            tasks.c.status.not_in(_TERMINAL),
        )
    ).mappings().all()

    for task in active_tasks:
        source = bind.execute(
            sa.select(
                requests.c.id,
                requests.c.ticket_id,
                requests.c.reason_code,
                requests.c.lock_version,
            )
            .where(
                requests.c.conversation_id
                == int(task["webchat_conversation_id"]),
                requests.c.status.in_(_OPEN_HANDOFF),
            )
            .order_by(requests.c.id.desc())
            .limit(1)
        ).mappings().first()
        if source is None:
            continue
        bind.execute(
            sa.update(tasks)
            .where(tasks.c.id == int(task["id"]))
            .values(
                source_type="webchat_handoff",
                source_id=str(source["id"]),
                source_version=int(source["lock_version"] or 1),
                projection_schema="nexus.operator-task.webchat-handoff.v2",
                ticket_id=(
                    int(source["ticket_id"])
                    if source["ticket_id"] is not None
                    else task["ticket_id"]
                ),
                reason_code=source["reason_code"] or task["reason_code"],
                priority=40,
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    tasks, _requests = _tables()
    bind.execute(
        sa.update(tasks)
        .where(
            tasks.c.task_type == "handoff",
            tasks.c.source_type == "webchat_handoff",
            tasks.c.webchat_conversation_id.is_not(None),
            tasks.c.status.not_in(_TERMINAL),
        )
        .values(
            source_type="webchat",
            source_id=sa.cast(tasks.c.webchat_conversation_id, sa.String()),
            source_version=None,
            projection_schema="nexus.operator-task-projection.v1",
            priority=100,
        )
    )
