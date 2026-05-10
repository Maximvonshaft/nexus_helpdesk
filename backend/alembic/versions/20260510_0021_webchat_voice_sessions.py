"""webchat voice session foundation

Revision ID: 20260510_0021
Revises: 20260507_0020
Create Date: 2026-05-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260510_0021"
down_revision = "20260507_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webchat_voice_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("webchat_conversations.id"), nullable=False),
        sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id"), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False, server_default="mock"),
        sa.Column("provider_room_name", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="created"),
        sa.Column("mode", sa.String(length=40), nullable=False, server_default="visitor_to_agent"),
        sa.Column("locale", sa.String(length=20), nullable=True),
        sa.Column("recording_consent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("recording_status", sa.String(length=40), nullable=False, server_default="disabled"),
        sa.Column("transcript_status", sa.String(length=40), nullable=False, server_default="disabled"),
        sa.Column("summary_status", sa.String(length=40), nullable=False, server_default="pending"),
        sa.Column("accepted_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("ended_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ringing_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("uq_webchat_voice_sessions_public_id", "webchat_voice_sessions", ["public_id"], unique=True)
    op.create_index("ix_voice_conversation_status", "webchat_voice_sessions", ["conversation_id", "status"], unique=False)
    op.create_index("ix_voice_ticket_status", "webchat_voice_sessions", ["ticket_id", "status"], unique=False)
    op.create_index("ix_voice_expires_status", "webchat_voice_sessions", ["expires_at", "status"], unique=False)
    op.create_index("ix_voice_accepted_by_user_id", "webchat_voice_sessions", ["accepted_by_user_id"], unique=False)
    op.create_index("ix_voice_created_at", "webchat_voice_sessions", ["created_at"], unique=False)

    op.create_table(
        "webchat_voice_participants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("voice_session_id", sa.Integer(), sa.ForeignKey("webchat_voice_sessions.id"), nullable=False),
        sa.Column("participant_type", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("visitor_label", sa.String(length=160), nullable=True),
        sa.Column("provider_identity", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="invited"),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("voice_session_id", "provider_identity", name="uq_voice_participant_session_identity"),
    )
    op.create_index("ix_voice_participants_session", "webchat_voice_participants", ["voice_session_id"], unique=False)
    op.create_index("ix_voice_participants_type", "webchat_voice_participants", ["participant_type"], unique=False)
    op.create_index("ix_voice_participants_identity", "webchat_voice_participants", ["provider_identity"], unique=False)
    op.create_index("ix_voice_participants_user_id", "webchat_voice_participants", ["user_id"], unique=False)

    op.create_table(
        "webchat_voice_transcript_segments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("voice_session_id", sa.Integer(), sa.ForeignKey("webchat_voice_sessions.id"), nullable=False),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("webchat_conversations.id"), nullable=False),
        sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id"), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("provider_session_id", sa.String(length=160), nullable=True),
        sa.Column("provider_item_id", sa.String(length=160), nullable=True),
        sa.Column("participant_identity", sa.String(length=160), nullable=False),
        sa.Column("speaker_type", sa.String(length=40), nullable=False),
        sa.Column("speaker_label", sa.String(length=160), nullable=True),
        sa.Column("segment_id", sa.String(length=160), nullable=False),
        sa.Column("language", sa.String(length=20), nullable=True),
        sa.Column("is_final", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("start_ms", sa.Integer(), nullable=True),
        sa.Column("end_ms", sa.Integer(), nullable=True),
        sa.Column("text_raw", sa.Text(), nullable=False),
        sa.Column("text_redacted", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Integer(), nullable=True),
        sa.Column("redaction_status", sa.String(length=40), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "provider",
            "provider_session_id",
            "segment_id",
            "participant_identity",
            name="uq_voice_transcript_provider_session_segment_participant",
        ),
    )
    op.create_index("ix_voice_transcript_session", "webchat_voice_transcript_segments", ["voice_session_id"], unique=False)
    op.create_index("ix_voice_transcript_conversation", "webchat_voice_transcript_segments", ["conversation_id"], unique=False)
    op.create_index("ix_voice_transcript_ticket", "webchat_voice_transcript_segments", ["ticket_id"], unique=False)
    op.create_index("ix_voice_transcript_provider", "webchat_voice_transcript_segments", ["provider"], unique=False)
    op.create_index("ix_voice_transcript_provider_session", "webchat_voice_transcript_segments", ["provider_session_id"], unique=False)
    op.create_index("ix_voice_transcript_participant", "webchat_voice_transcript_segments", ["participant_identity"], unique=False)
    op.create_index("ix_voice_transcript_segment", "webchat_voice_transcript_segments", ["segment_id"], unique=False)
    op.create_index("ix_voice_transcript_language", "webchat_voice_transcript_segments", ["language"], unique=False)
    op.create_index("ix_voice_transcript_is_final", "webchat_voice_transcript_segments", ["is_final"], unique=False)
    op.create_index("ix_voice_transcript_created_at", "webchat_voice_transcript_segments", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_voice_transcript_created_at", table_name="webchat_voice_transcript_segments")
    op.drop_index("ix_voice_transcript_is_final", table_name="webchat_voice_transcript_segments")
    op.drop_index("ix_voice_transcript_language", table_name="webchat_voice_transcript_segments")
    op.drop_index("ix_voice_transcript_segment", table_name="webchat_voice_transcript_segments")
    op.drop_index("ix_voice_transcript_participant", table_name="webchat_voice_transcript_segments")
    op.drop_index("ix_voice_transcript_provider_session", table_name="webchat_voice_transcript_segments")
    op.drop_index("ix_voice_transcript_provider", table_name="webchat_voice_transcript_segments")
    op.drop_index("ix_voice_transcript_ticket", table_name="webchat_voice_transcript_segments")
    op.drop_index("ix_voice_transcript_conversation", table_name="webchat_voice_transcript_segments")
    op.drop_index("ix_voice_transcript_session", table_name="webchat_voice_transcript_segments")
    op.drop_table("webchat_voice_transcript_segments")

    op.drop_index("ix_voice_participants_user_id", table_name="webchat_voice_participants")
    op.drop_index("ix_voice_participants_identity", table_name="webchat_voice_participants")
    op.drop_index("ix_voice_participants_type", table_name="webchat_voice_participants")
    op.drop_index("ix_voice_participants_session", table_name="webchat_voice_participants")
    op.drop_table("webchat_voice_participants")

    op.drop_index("ix_voice_created_at", table_name="webchat_voice_sessions")
    op.drop_index("ix_voice_accepted_by_user_id", table_name="webchat_voice_sessions")
    op.drop_index("ix_voice_expires_status", table_name="webchat_voice_sessions")
    op.drop_index("ix_voice_ticket_status", table_name="webchat_voice_sessions")
    op.drop_index("ix_voice_conversation_status", table_name="webchat_voice_sessions")
    op.drop_index("uq_webchat_voice_sessions_public_id", table_name="webchat_voice_sessions")
    op.drop_table("webchat_voice_sessions")
