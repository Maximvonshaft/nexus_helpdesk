"""webcall session action commands

Revision ID: 20260529_0042
Revises: 20260529_0041
Create Date: 2026-05-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260529_0042"
down_revision = "20260529_0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webchat_voice_session_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("voice_session_id", sa.Integer(), sa.ForeignKey("webchat_voice_sessions.id"), nullable=False),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("webchat_conversations.id"), nullable=False),
        sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id"), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="recorded"),
        sa.Column("provider_status", sa.String(length=40), nullable=False, server_default="not_executed"),
        sa.Column("provider_reason", sa.String(length=160), nullable=False, server_default="provider_adapter_pending"),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column("ticket_event_id", sa.Integer(), sa.ForeignKey("ticket_events.id"), nullable=True),
        sa.Column("webchat_event_id", sa.Integer(), sa.ForeignKey("webchat_events.id"), nullable=True),
        sa.Column("audit_id", sa.Integer(), sa.ForeignKey("admin_audit_logs.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_webchat_voice_session_actions_voice_session_id", "webchat_voice_session_actions", ["voice_session_id"], unique=False)
    op.create_index("ix_webchat_voice_session_actions_conversation_id", "webchat_voice_session_actions", ["conversation_id"], unique=False)
    op.create_index("ix_webchat_voice_session_actions_ticket_id", "webchat_voice_session_actions", ["ticket_id"], unique=False)
    op.create_index("ix_webchat_voice_session_actions_actor_user_id", "webchat_voice_session_actions", ["actor_user_id"], unique=False)
    op.create_index("ix_webchat_voice_session_actions_action_type", "webchat_voice_session_actions", ["action_type"], unique=False)
    op.create_index("ix_webchat_voice_session_actions_status", "webchat_voice_session_actions", ["status"], unique=False)
    op.create_index("ix_webchat_voice_session_actions_provider_status", "webchat_voice_session_actions", ["provider_status"], unique=False)
    op.create_index("ix_webchat_voice_session_actions_provider_reason", "webchat_voice_session_actions", ["provider_reason"], unique=False)
    op.create_index("ix_webchat_voice_session_actions_ticket_event_id", "webchat_voice_session_actions", ["ticket_event_id"], unique=False)
    op.create_index("ix_webchat_voice_session_actions_webchat_event_id", "webchat_voice_session_actions", ["webchat_event_id"], unique=False)
    op.create_index("ix_webchat_voice_session_actions_audit_id", "webchat_voice_session_actions", ["audit_id"], unique=False)
    op.create_index("ix_webchat_voice_session_actions_created_at", "webchat_voice_session_actions", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_webchat_voice_session_actions_created_at", table_name="webchat_voice_session_actions")
    op.drop_index("ix_webchat_voice_session_actions_audit_id", table_name="webchat_voice_session_actions")
    op.drop_index("ix_webchat_voice_session_actions_webchat_event_id", table_name="webchat_voice_session_actions")
    op.drop_index("ix_webchat_voice_session_actions_ticket_event_id", table_name="webchat_voice_session_actions")
    op.drop_index("ix_webchat_voice_session_actions_provider_reason", table_name="webchat_voice_session_actions")
    op.drop_index("ix_webchat_voice_session_actions_provider_status", table_name="webchat_voice_session_actions")
    op.drop_index("ix_webchat_voice_session_actions_status", table_name="webchat_voice_session_actions")
    op.drop_index("ix_webchat_voice_session_actions_action_type", table_name="webchat_voice_session_actions")
    op.drop_index("ix_webchat_voice_session_actions_actor_user_id", table_name="webchat_voice_session_actions")
    op.drop_index("ix_webchat_voice_session_actions_ticket_id", table_name="webchat_voice_session_actions")
    op.drop_index("ix_webchat_voice_session_actions_conversation_id", table_name="webchat_voice_session_actions")
    op.drop_index("ix_webchat_voice_session_actions_voice_session_id", table_name="webchat_voice_session_actions")
    op.drop_table("webchat_voice_session_actions")
