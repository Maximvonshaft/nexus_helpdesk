"""query indexes for cursor pagination

Revision ID: 20260506_0020
Revises: 20260506_0019
Create Date: 2026-05-06
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260506_0020"
down_revision = "20260506_0019"
branch_labels = None
depends_on = None

INDEXES = [
    ("ix_webchat_messages_conversation_id_id", "webchat_messages", ["conversation_id", "id"]),
    ("ix_webchat_events_conversation_id_id", "webchat_events", ["conversation_id", "id"]),
    ("ix_webchat_events_ticket_id_id", "webchat_events", ["ticket_id", "id"]),
    ("ix_webchat_conversations_id_updated", "webchat_conversations", ["id", "updated_at"]),
    ("ix_webchat_conversations_ticket_id", "webchat_conversations", ["ticket_id"]),
    ("ix_background_jobs_status_next_type_created", "background_jobs", ["status", "next_run_at", "job_type", "created_at"]),
    ("ix_tickets_status_updated_id", "tickets", ["status", "updated_at", "id"]),
    ("ix_tickets_assignee_status_updated_id", "tickets", ["assignee_id", "status", "updated_at", "id"]),
    ("ix_ticket_events_ticket_created_id", "ticket_events", ["ticket_id", "created_at", "id"]),
    ("ix_tool_call_logs_tool_status_created", "tool_call_logs", ["tool_name", "status", "created_at"]),
]


def _table_names(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _index_names(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return set()
    return {idx["name"] for idx in inspector.get_indexes(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    tables = _table_names(bind)
    for name, table, cols in INDEXES:
        if table in tables and name not in _index_names(bind, table):
            op.create_index(name, table, cols)


def downgrade() -> None:
    bind = op.get_bind()
    tables = _table_names(bind)
    for name, table, _cols in reversed(INDEXES):
        if table in tables and name in _index_names(bind, table):
            op.drop_index(name, table_name=table)
