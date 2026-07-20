"""Conversation-first WebChat and operator capacity authority.

Revision ID: 20260720_0064
Revises: 20260720_0063
Create Date: 2026-07-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260720_0064"
down_revision = "20260720_0063"
branch_labels = None
depends_on = None


_TICKET_OPTIONAL_TABLES = (
    "webchat_ai_turns",
    "webchat_events",
    "webchat_handoff_requests",
    "webchat_card_actions",
    "webchat_ai_debug_runs",
    "webchat_ai_test_findings",
    "webchat_voice_sessions",
    "webchat_voice_transcript_segments",
    "webchat_voice_session_actions",
)


def _set_ticket_nullable(table_name: str, *, nullable: bool) -> None:
    with op.batch_alter_table(table_name) as batch:
        batch.alter_column(
            "ticket_id",
            existing_type=sa.Integer(),
            nullable=nullable,
        )


def upgrade() -> None:
    for table_name in _TICKET_OPTIONAL_TABLES:
        _set_ticket_nullable(table_name, nullable=True)

    op.create_table(
        "conversation_controls",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "conversation_id",
            sa.Integer(),
            sa.ForeignKey("webchat_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "customer_id",
            sa.Integer(),
            sa.ForeignKey("customers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("tenant_key", sa.String(length=120), nullable=False),
        sa.Column("country_code", sa.String(length=16), nullable=True),
        sa.Column("channel_key", sa.String(length=120), nullable=False),
        sa.Column("outcome", sa.String(length=64), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "closed_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("closure_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "conversation_id",
            name="uq_conversation_controls_conversation",
        ),
    )
    op.create_index(
        "ix_conversation_controls_conversation_id",
        "conversation_controls",
        ["conversation_id"],
    )
    op.create_index(
        "ix_conversation_controls_customer",
        "conversation_controls",
        ["customer_id"],
    )
    op.create_index(
        "ix_conversation_controls_scope",
        "conversation_controls",
        ["tenant_key", "country_code", "channel_key"],
    )
    op.create_index(
        "ix_conversation_controls_outcome",
        "conversation_controls",
        ["outcome", "closed_at"],
    )
    op.create_index(
        "ix_conversation_controls_country_code",
        "conversation_controls",
        ["country_code"],
    )
    op.create_index(
        "ix_conversation_controls_closed_at",
        "conversation_controls",
        ["closed_at"],
    )
    op.create_index(
        "ix_conversation_controls_closed_by_user_id",
        "conversation_controls",
        ["closed_by_user_id"],
    )

    op.create_table(
        "operator_agent_states",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=24),
            nullable=False,
            server_default="offline",
        ),
        sa.Column(
            "max_concurrent_conversations",
            sa.Integer(),
            nullable=False,
            server_default="3",
        ),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status_changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "status IN ('offline', 'online', 'paused')",
            name="ck_operator_agent_states_status",
        ),
        sa.CheckConstraint(
            "max_concurrent_conversations BETWEEN 1 AND 20",
            name="ck_operator_agent_states_capacity",
        ),
        sa.UniqueConstraint("user_id", name="uq_operator_agent_states_user"),
    )
    op.create_index(
        "ix_operator_agent_states_user_id",
        "operator_agent_states",
        ["user_id"],
    )
    op.create_index(
        "ix_operator_agent_states_status",
        "operator_agent_states",
        ["status"],
    )
    op.create_index(
        "ix_operator_agent_states_last_heartbeat_at",
        "operator_agent_states",
        ["last_heartbeat_at"],
    )
    op.create_index(
        "ix_operator_agent_states_status_heartbeat",
        "operator_agent_states",
        ["status", "last_heartbeat_at"],
    )

    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            INSERT INTO conversation_controls (
                conversation_id,
                customer_id,
                tenant_key,
                country_code,
                channel_key,
                created_at,
                updated_at
            )
            SELECT
                conversations.id,
                tickets.customer_id,
                conversations.tenant_key,
                tickets.country_code,
                conversations.channel_key,
                COALESCE(conversations.created_at, CURRENT_TIMESTAMP),
                COALESCE(conversations.updated_at, CURRENT_TIMESTAMP)
            FROM webchat_conversations AS conversations
            LEFT JOIN tickets ON tickets.id = conversations.ticket_id
            WHERE NOT EXISTS (
                SELECT 1
                FROM conversation_controls AS controls
                WHERE controls.conversation_id = conversations.id
            )
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    missing_ticket_links = bind.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM webchat_conversations
            WHERE ticket_id IS NULL
            """
        )
    ).scalar_one()
    if int(missing_ticket_links or 0) != 0:
        raise RuntimeError(
            "migration_0064_downgrade_blocked: ticketless conversations exist"
        )

    for table_name in _TICKET_OPTIONAL_TABLES:
        null_count = bind.execute(
            sa.text(f"SELECT COUNT(*) FROM {table_name} WHERE ticket_id IS NULL")
        ).scalar_one()
        if int(null_count or 0) != 0:
            raise RuntimeError(
                f"migration_0064_downgrade_blocked: {table_name} has ticketless rows"
            )

    op.drop_index(
        "ix_operator_agent_states_status_heartbeat",
        table_name="operator_agent_states",
    )
    op.drop_index(
        "ix_operator_agent_states_last_heartbeat_at",
        table_name="operator_agent_states",
    )
    op.drop_index(
        "ix_operator_agent_states_status",
        table_name="operator_agent_states",
    )
    op.drop_index(
        "ix_operator_agent_states_user_id",
        table_name="operator_agent_states",
    )
    op.drop_table("operator_agent_states")

    op.drop_index(
        "ix_conversation_controls_closed_by_user_id",
        table_name="conversation_controls",
    )
    op.drop_index(
        "ix_conversation_controls_closed_at",
        table_name="conversation_controls",
    )
    op.drop_index(
        "ix_conversation_controls_country_code",
        table_name="conversation_controls",
    )
    op.drop_index(
        "ix_conversation_controls_outcome",
        table_name="conversation_controls",
    )
    op.drop_index(
        "ix_conversation_controls_scope",
        table_name="conversation_controls",
    )
    op.drop_index(
        "ix_conversation_controls_customer",
        table_name="conversation_controls",
    )
    op.drop_index(
        "ix_conversation_controls_conversation_id",
        table_name="conversation_controls",
    )
    op.drop_table("conversation_controls")

    for table_name in reversed(_TICKET_OPTIONAL_TABLES):
        _set_ticket_nullable(table_name, nullable=False)
