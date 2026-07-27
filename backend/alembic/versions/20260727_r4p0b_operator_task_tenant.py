"""Add relational Tenant ownership while preserving the isolated shadow scope.

Revision ID: 20260727_r4p0b
Revises: 20260727_r4p0
Create Date: 2026-07-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260727_r4p0b"
down_revision = "20260727_r4p0"
branch_labels = None
depends_on = None

ACTIVE_TASK_SQL = (
    "status NOT IN ('resolved', 'dropped', 'replayed', "
    "'replay_failed', 'cancelled')"
)


def _backfill_postgresql(bind) -> None:
    bind.execute(
        sa.text(
            "UPDATE operator_tasks AS task "
            "SET tenant_id = ticket.tenant_id "
            "FROM tickets AS ticket "
            "WHERE task.ticket_id = ticket.id "
            "AND task.tenant_id IS NULL "
            "AND ticket.tenant_id IS NOT NULL"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE operator_tasks AS task "
            "SET tenant_id = tenant.id "
            "FROM webchat_conversations AS conversation "
            "JOIN tenants AS tenant "
            "ON tenant.tenant_key = conversation.tenant_key "
            "WHERE task.webchat_conversation_id = conversation.id "
            "AND task.tenant_id IS NULL"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE operator_tasks AS task "
            "SET tenant_id = actor.tenant_id "
            "FROM users AS actor "
            "WHERE task.assignee_id = actor.id "
            "AND task.tenant_id IS NULL "
            "AND actor.tenant_id IS NOT NULL"
        )
    )


def _backfill_sqlite(bind) -> None:
    bind.execute(
        sa.text(
            "UPDATE operator_tasks SET tenant_id = ("
            " SELECT tickets.tenant_id FROM tickets"
            " WHERE tickets.id = operator_tasks.ticket_id"
            ") WHERE tenant_id IS NULL AND ticket_id IS NOT NULL"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE operator_tasks SET tenant_id = ("
            " SELECT tenants.id"
            " FROM webchat_conversations"
            " JOIN tenants ON tenants.tenant_key = webchat_conversations.tenant_key"
            " WHERE webchat_conversations.id = operator_tasks.webchat_conversation_id"
            ") WHERE tenant_id IS NULL AND webchat_conversation_id IS NOT NULL"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE operator_tasks SET tenant_id = ("
            " SELECT users.tenant_id FROM users"
            " WHERE users.id = operator_tasks.assignee_id"
            ") WHERE tenant_id IS NULL AND assignee_id IS NOT NULL"
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    op.drop_index(
        "uq_operator_tasks_active_webchat_handoff",
        table_name="operator_tasks",
    )
    op.drop_index(
        "uq_operator_tasks_active_source",
        table_name="operator_tasks",
    )
    op.drop_index(
        "ix_operator_tasks_status_priority_created",
        table_name="operator_tasks",
    )
    op.add_column(
        "operator_tasks",
        sa.Column("tenant_id", sa.Integer(), nullable=True),
    )

    if dialect.startswith("postgresql"):
        _backfill_postgresql(bind)
    else:
        _backfill_sqlite(bind)

    # A remaining NULL is not platform scope. It is the one isolated legacy
    # shadow domain and is authorized only when runtime authority mode is
    # explicitly ``shadow``. Production ``enforce`` mode cannot access it.
    if dialect == "sqlite":
        with op.batch_alter_table("operator_tasks") as batch:
            batch.create_foreign_key(
                "fk_operator_tasks_tenant_id_tenants",
                "tenants",
                ["tenant_id"],
                ["id"],
                ondelete="RESTRICT",
            )
    else:
        op.create_foreign_key(
            "fk_operator_tasks_tenant_id_tenants",
            "operator_tasks",
            "tenants",
            ["tenant_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    op.create_index(
        "ix_operator_tasks_tenant_id",
        "operator_tasks",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_operator_tasks_tenant_status_priority_created",
        "operator_tasks",
        ["tenant_id", "status", "priority", "created_at"],
        unique=False,
    )
    op.create_index(
        "uq_operator_tasks_active_webchat_handoff",
        "operator_tasks",
        ["tenant_id", "webchat_conversation_id", "task_type"],
        unique=True,
        postgresql_where=sa.text(
            f"tenant_id IS NOT NULL AND webchat_conversation_id IS NOT NULL AND {ACTIVE_TASK_SQL}"
        ),
        sqlite_where=sa.text(
            f"tenant_id IS NOT NULL AND webchat_conversation_id IS NOT NULL AND {ACTIVE_TASK_SQL}"
        ),
    )
    op.create_index(
        "uq_operator_tasks_active_webchat_handoff_shadow",
        "operator_tasks",
        ["webchat_conversation_id", "task_type"],
        unique=True,
        postgresql_where=sa.text(
            f"tenant_id IS NULL AND webchat_conversation_id IS NOT NULL AND {ACTIVE_TASK_SQL}"
        ),
        sqlite_where=sa.text(
            f"tenant_id IS NULL AND webchat_conversation_id IS NOT NULL AND {ACTIVE_TASK_SQL}"
        ),
    )
    op.create_index(
        "uq_operator_tasks_active_source",
        "operator_tasks",
        ["tenant_id", "source_type", "source_id", "task_type"],
        unique=True,
        postgresql_where=sa.text(
            f"tenant_id IS NOT NULL AND source_id IS NOT NULL AND {ACTIVE_TASK_SQL}"
        ),
        sqlite_where=sa.text(
            f"tenant_id IS NOT NULL AND source_id IS NOT NULL AND {ACTIVE_TASK_SQL}"
        ),
    )
    op.create_index(
        "uq_operator_tasks_active_source_shadow",
        "operator_tasks",
        ["source_type", "source_id", "task_type"],
        unique=True,
        postgresql_where=sa.text(
            f"tenant_id IS NULL AND source_id IS NOT NULL AND {ACTIVE_TASK_SQL}"
        ),
        sqlite_where=sa.text(
            f"tenant_id IS NULL AND source_id IS NOT NULL AND {ACTIVE_TASK_SQL}"
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    for name in (
        "uq_operator_tasks_active_webchat_handoff_shadow",
        "uq_operator_tasks_active_webchat_handoff",
        "uq_operator_tasks_active_source_shadow",
        "uq_operator_tasks_active_source",
        "ix_operator_tasks_tenant_status_priority_created",
        "ix_operator_tasks_tenant_id",
    ):
        op.drop_index(name, table_name="operator_tasks")

    op.create_index(
        "ix_operator_tasks_status_priority_created",
        "operator_tasks",
        ["status", "priority", "created_at"],
        unique=False,
    )
    op.create_index(
        "uq_operator_tasks_active_webchat_handoff",
        "operator_tasks",
        ["webchat_conversation_id", "task_type"],
        unique=True,
        postgresql_where=sa.text(
            f"webchat_conversation_id IS NOT NULL AND {ACTIVE_TASK_SQL}"
        ),
        sqlite_where=sa.text(
            f"webchat_conversation_id IS NOT NULL AND {ACTIVE_TASK_SQL}"
        ),
    )
    op.create_index(
        "uq_operator_tasks_active_source",
        "operator_tasks",
        ["source_type", "source_id", "task_type"],
        unique=True,
        postgresql_where=sa.text(
            f"source_id IS NOT NULL AND {ACTIVE_TASK_SQL}"
        ),
        sqlite_where=sa.text(
            f"source_id IS NOT NULL AND {ACTIVE_TASK_SQL}"
        ),
    )

    if dialect == "sqlite":
        with op.batch_alter_table("operator_tasks") as batch:
            batch.drop_constraint(
                "fk_operator_tasks_tenant_id_tenants",
                type_="foreignkey",
            )
            batch.drop_column("tenant_id")
    else:
        op.drop_constraint(
            "fk_operator_tasks_tenant_id_tenants",
            "operator_tasks",
            type_="foreignkey",
        )
        op.drop_column("operator_tasks", "tenant_id")
