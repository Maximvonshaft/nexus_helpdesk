"""core api and webchat performance indexes

Revision ID: 20260507_0020
Revises: 20260506_0019
Create Date: 2026-05-07
"""

from __future__ import annotations

from alembic import op

revision = "20260507_0020"
down_revision = "20260506_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_webchat_events_conversation_id_id",
        "webchat_events",
        ["conversation_id", "id"],
        unique=False,
    )
    op.create_index(
        "ix_webchat_events_ticket_id_id",
        "webchat_events",
        ["ticket_id", "id"],
        unique=False,
    )
    op.create_index(
        "ix_tickets_updated_at_id",
        "tickets",
        ["updated_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_tickets_status_updated_at_id",
        "tickets",
        ["status", "updated_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_tickets_assignee_status_updated_id",
        "tickets",
        ["assignee_id", "status", "updated_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_tickets_team_status_updated_id",
        "tickets",
        ["team_id", "status", "updated_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_tickets_team_status_updated_id", table_name="tickets")
    op.drop_index("ix_tickets_assignee_status_updated_id", table_name="tickets")
    op.drop_index("ix_tickets_status_updated_at_id", table_name="tickets")
    op.drop_index("ix_tickets_updated_at_id", table_name="tickets")
    op.drop_index("ix_webchat_events_ticket_id_id", table_name="webchat_events")
    op.drop_index("ix_webchat_events_conversation_id_id", table_name="webchat_events")
