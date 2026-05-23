"""webcall ai agent foundation

Revision ID: 20260523_wcall_ai1
Revises: 20260522_0031
Create Date: 2026-05-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260523_wcall_ai1"
down_revision = "20260522_0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("webchat_voice_sessions", sa.Column("ai_agent_status", sa.String(length=40), nullable=True))
    op.add_column("webchat_voice_sessions", sa.Column("ai_agent_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("webchat_voice_sessions", sa.Column("ai_agent_ended_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("webchat_voice_sessions", sa.Column("ai_handoff_reason", sa.String(length=240), nullable=True))
    op.add_column("webchat_voice_sessions", sa.Column("ai_language", sa.String(length=20), nullable=True))
    op.add_column(
        "webchat_voice_sessions",
        sa.Column("ai_turn_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_webchat_voice_sessions_ai_agent_status", "webchat_voice_sessions", ["ai_agent_status"], unique=False)
    op.create_index("ix_webchat_voice_sessions_ai_language", "webchat_voice_sessions", ["ai_language"], unique=False)

    op.create_table(
        "webchat_voice_ai_turns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("voice_session_id", sa.Integer(), sa.ForeignKey("webchat_voice_sessions.id"), nullable=False),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("webchat_conversations.id"), nullable=True),
        sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id"), nullable=True),
        sa.Column("turn_index", sa.Integer(), nullable=False),
        sa.Column("customer_text_redacted", sa.Text(), nullable=True),
        sa.Column("ai_response_text_redacted", sa.Text(), nullable=True),
        sa.Column("language", sa.String(length=20), nullable=True),
        sa.Column("intent", sa.String(length=80), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=True),
        sa.Column("tracking_number_hash", sa.String(length=64), nullable=True),
        sa.Column("handoff_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("handoff_reason", sa.String(length=160), nullable=True),
        sa.Column("confidence", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=80), nullable=True),
        sa.Column("stt_provider", sa.String(length=80), nullable=True),
        sa.Column("tts_provider", sa.String(length=80), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("voice_session_id", "turn_index", name="uq_voice_ai_turn_session_index"),
    )
    op.create_index("ix_webchat_voice_ai_turns_voice_session_id", "webchat_voice_ai_turns", ["voice_session_id"], unique=False)
    op.create_index("ix_webchat_voice_ai_turns_conversation_id", "webchat_voice_ai_turns", ["conversation_id"], unique=False)
    op.create_index("ix_webchat_voice_ai_turns_ticket_id", "webchat_voice_ai_turns", ["ticket_id"], unique=False)
    op.create_index("ix_webchat_voice_ai_turns_turn_index", "webchat_voice_ai_turns", ["turn_index"], unique=False)
    op.create_index("ix_webchat_voice_ai_turns_language", "webchat_voice_ai_turns", ["language"], unique=False)
    op.create_index("ix_webchat_voice_ai_turns_intent", "webchat_voice_ai_turns", ["intent"], unique=False)
    op.create_index("ix_webchat_voice_ai_turns_action", "webchat_voice_ai_turns", ["action"], unique=False)
    op.create_index(
        "ix_webchat_voice_ai_turns_tracking_number_hash",
        "webchat_voice_ai_turns",
        ["tracking_number_hash"],
        unique=False,
    )
    op.create_index(
        "ix_webchat_voice_ai_turns_handoff_required",
        "webchat_voice_ai_turns",
        ["handoff_required"],
        unique=False,
    )
    op.create_index("ix_webchat_voice_ai_turns_created_at", "webchat_voice_ai_turns", ["created_at"], unique=False)

    op.create_table(
        "webchat_voice_ai_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("voice_session_id", sa.Integer(), sa.ForeignKey("webchat_voice_sessions.id"), nullable=False),
        sa.Column("turn_id", sa.Integer(), sa.ForeignKey("webchat_voice_ai_turns.id"), nullable=True),
        sa.Column("model_action", sa.String(length=80), nullable=False),
        sa.Column("nexus_decision", sa.String(length=40), nullable=False),
        sa.Column("decision_reason", sa.String(length=240), nullable=True),
        sa.Column("speedaf_tool_name", sa.String(length=160), nullable=True),
        sa.Column("background_job_id", sa.Integer(), sa.ForeignKey("background_jobs.id"), nullable=True),
        sa.Column("tool_call_log_id", sa.Integer(), nullable=True),
        sa.Column("result_status", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_webchat_voice_ai_actions_voice_session_id", "webchat_voice_ai_actions", ["voice_session_id"], unique=False)
    op.create_index("ix_webchat_voice_ai_actions_turn_id", "webchat_voice_ai_actions", ["turn_id"], unique=False)
    op.create_index("ix_webchat_voice_ai_actions_model_action", "webchat_voice_ai_actions", ["model_action"], unique=False)
    op.create_index("ix_webchat_voice_ai_actions_nexus_decision", "webchat_voice_ai_actions", ["nexus_decision"], unique=False)
    op.create_index("ix_webchat_voice_ai_actions_speedaf_tool_name", "webchat_voice_ai_actions", ["speedaf_tool_name"], unique=False)
    op.create_index("ix_webchat_voice_ai_actions_background_job_id", "webchat_voice_ai_actions", ["background_job_id"], unique=False)
    op.create_index("ix_webchat_voice_ai_actions_tool_call_log_id", "webchat_voice_ai_actions", ["tool_call_log_id"], unique=False)
    op.create_index("ix_webchat_voice_ai_actions_result_status", "webchat_voice_ai_actions", ["result_status"], unique=False)
    op.create_index("ix_webchat_voice_ai_actions_created_at", "webchat_voice_ai_actions", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_webchat_voice_ai_actions_created_at", table_name="webchat_voice_ai_actions")
    op.drop_index("ix_webchat_voice_ai_actions_result_status", table_name="webchat_voice_ai_actions")
    op.drop_index("ix_webchat_voice_ai_actions_tool_call_log_id", table_name="webchat_voice_ai_actions")
    op.drop_index("ix_webchat_voice_ai_actions_background_job_id", table_name="webchat_voice_ai_actions")
    op.drop_index("ix_webchat_voice_ai_actions_speedaf_tool_name", table_name="webchat_voice_ai_actions")
    op.drop_index("ix_webchat_voice_ai_actions_nexus_decision", table_name="webchat_voice_ai_actions")
    op.drop_index("ix_webchat_voice_ai_actions_model_action", table_name="webchat_voice_ai_actions")
    op.drop_index("ix_webchat_voice_ai_actions_turn_id", table_name="webchat_voice_ai_actions")
    op.drop_index("ix_webchat_voice_ai_actions_voice_session_id", table_name="webchat_voice_ai_actions")
    op.drop_table("webchat_voice_ai_actions")

    op.drop_index("ix_webchat_voice_ai_turns_created_at", table_name="webchat_voice_ai_turns")
    op.drop_index("ix_webchat_voice_ai_turns_handoff_required", table_name="webchat_voice_ai_turns")
    op.drop_index("ix_webchat_voice_ai_turns_tracking_number_hash", table_name="webchat_voice_ai_turns")
    op.drop_index("ix_webchat_voice_ai_turns_action", table_name="webchat_voice_ai_turns")
    op.drop_index("ix_webchat_voice_ai_turns_intent", table_name="webchat_voice_ai_turns")
    op.drop_index("ix_webchat_voice_ai_turns_language", table_name="webchat_voice_ai_turns")
    op.drop_index("ix_webchat_voice_ai_turns_turn_index", table_name="webchat_voice_ai_turns")
    op.drop_index("ix_webchat_voice_ai_turns_ticket_id", table_name="webchat_voice_ai_turns")
    op.drop_index("ix_webchat_voice_ai_turns_conversation_id", table_name="webchat_voice_ai_turns")
    op.drop_index("ix_webchat_voice_ai_turns_voice_session_id", table_name="webchat_voice_ai_turns")
    op.drop_table("webchat_voice_ai_turns")

    op.drop_index("ix_webchat_voice_sessions_ai_language", table_name="webchat_voice_sessions")
    op.drop_index("ix_webchat_voice_sessions_ai_agent_status", table_name="webchat_voice_sessions")
    op.drop_column("webchat_voice_sessions", "ai_turn_count")
    op.drop_column("webchat_voice_sessions", "ai_language")
    op.drop_column("webchat_voice_sessions", "ai_handoff_reason")
    op.drop_column("webchat_voice_sessions", "ai_agent_ended_at")
    op.drop_column("webchat_voice_sessions", "ai_agent_started_at")
    op.drop_column("webchat_voice_sessions", "ai_agent_status")
