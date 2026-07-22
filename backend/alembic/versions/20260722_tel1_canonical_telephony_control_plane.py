"""Canonical LiveKit telephony and voice-routing control plane.

Revision ID: 20260722_tel1
Revises: 20260721_0073
Create Date: 2026-07-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260722_tel1"
down_revision = "20260721_0073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("operator_agent_states") as batch:
        batch.add_column(
            sa.Column(
                "max_concurrent_voice_calls",
                sa.Integer(),
                nullable=False,
                server_default="1",
            )
        )
        batch.add_column(
            sa.Column(
                "voice_wrap_up_seconds",
                sa.Integer(),
                nullable=False,
                server_default="30",
            )
        )
        batch.create_check_constraint(
            "ck_operator_agent_states_voice_capacity",
            "max_concurrent_voice_calls BETWEEN 1 AND 5",
        )
        batch.create_check_constraint(
            "ck_operator_agent_states_voice_wrap_up",
            "voice_wrap_up_seconds BETWEEN 0 AND 900",
        )

    with op.batch_alter_table("webchat_voice_sessions") as batch:
        batch.add_column(sa.Column("handoff_request_id", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("direction", sa.String(length=16), nullable=False, server_default="inbound")
        )
        batch.add_column(sa.Column("provider_call_id", sa.String(length=160), nullable=True))
        batch.add_column(sa.Column("caller_number_hash", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("called_number", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("wrap_up_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key(
            "fk_voice_session_handoff_request",
            "webchat_handoff_requests",
            ["handoff_request_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_webchat_voice_sessions_handoff_request_id", ["handoff_request_id"])
        batch.create_index("ix_webchat_voice_sessions_provider_call_id", ["provider_call_id"])
        batch.create_index("ix_webchat_voice_sessions_caller_number_hash", ["caller_number_hash"])
        batch.create_index("ix_webchat_voice_sessions_called_number", ["called_number"])
        batch.create_index("ix_webchat_voice_sessions_wrap_up_expires_at", ["wrap_up_expires_at"])

    with op.batch_alter_table("webchat_voice_session_actions") as batch:
        batch.add_column(sa.Column("idempotency_key", sa.String(length=160), nullable=True))
        batch.add_column(sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_unique_constraint(
            "uq_voice_session_action_idempotency_key",
            ["idempotency_key"],
        )
        batch.create_index("ix_voice_session_actions_status_created", ["status", "created_at"])

    op.create_table(
        "voice_channel_configurations",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "channel_account_id",
            sa.Integer(),
            sa.ForeignKey("channel_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("inbound_trunk_id", sa.String(length=160), nullable=True),
        sa.Column("outbound_trunk_id", sa.String(length=160), nullable=True),
        sa.Column("routing_mode", sa.String(length=24), nullable=False, server_default="ai_first"),
        sa.Column("ai_agent_name", sa.String(length=160), nullable=True),
        sa.Column("queue_timeout_seconds", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("wrap_up_seconds", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("recording_policy", sa.String(length=32), nullable=False, server_default="disabled"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("channel_account_id", name="uq_voice_channel_configuration_account"),
        sa.CheckConstraint(
            "routing_mode IN ('ai_first', 'human_first')",
            name="ck_voice_channel_configuration_routing_mode",
        ),
        sa.CheckConstraint(
            "queue_timeout_seconds BETWEEN 15 AND 3600",
            name="ck_voice_channel_configuration_queue_timeout",
        ),
        sa.CheckConstraint(
            "wrap_up_seconds BETWEEN 0 AND 900",
            name="ck_voice_channel_configuration_wrap_up",
        ),
        sa.CheckConstraint(
            "recording_policy IN ('disabled', 'consent_required')",
            name="ck_voice_channel_configuration_recording_policy",
        ),
    )
    op.create_index(
        "ix_voice_channel_configurations_channel_account_id",
        "voice_channel_configurations",
        ["channel_account_id"],
    )
    op.create_index(
        "ix_voice_channel_configurations_enabled",
        "voice_channel_configurations",
        ["enabled"],
    )
    op.create_index(
        "ix_voice_channel_configurations_inbound_trunk_id",
        "voice_channel_configurations",
        ["inbound_trunk_id"],
    )

    op.create_table(
        "telephony_event_inbox",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("provider_event_id", sa.String(length=180), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("safe_payload_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="received"),
        sa.Column(
            "voice_session_id",
            sa.Integer(),
            sa.ForeignKey("webchat_voice_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error_code", sa.String(length=120), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "provider",
            "provider_event_id",
            name="uq_telephony_event_provider_identity",
        ),
        sa.CheckConstraint(
            "status IN ('received', 'processed', 'ignored', 'failed')",
            name="ck_telephony_event_inbox_status",
        ),
    )
    op.create_index(
        "ix_telephony_event_inbox_status_received",
        "telephony_event_inbox",
        ["status", "received_at"],
    )
    op.create_index(
        "ix_telephony_event_inbox_voice_session_id",
        "telephony_event_inbox",
        ["voice_session_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_telephony_event_inbox_voice_session_id", table_name="telephony_event_inbox")
    op.drop_index("ix_telephony_event_inbox_status_received", table_name="telephony_event_inbox")
    op.drop_table("telephony_event_inbox")

    op.drop_index("ix_voice_channel_configurations_inbound_trunk_id", table_name="voice_channel_configurations")
    op.drop_index("ix_voice_channel_configurations_enabled", table_name="voice_channel_configurations")
    op.drop_index("ix_voice_channel_configurations_channel_account_id", table_name="voice_channel_configurations")
    op.drop_table("voice_channel_configurations")

    with op.batch_alter_table("webchat_voice_session_actions") as batch:
        batch.drop_index("ix_voice_session_actions_status_created")
        batch.drop_constraint("uq_voice_session_action_idempotency_key", type_="unique")
        batch.drop_column("completed_at")
        batch.drop_column("last_attempt_at")
        batch.drop_column("attempt_count")
        batch.drop_column("idempotency_key")

    with op.batch_alter_table("webchat_voice_sessions") as batch:
        batch.drop_index("ix_webchat_voice_sessions_wrap_up_expires_at")
        batch.drop_index("ix_webchat_voice_sessions_called_number")
        batch.drop_index("ix_webchat_voice_sessions_caller_number_hash")
        batch.drop_index("ix_webchat_voice_sessions_provider_call_id")
        batch.drop_index("ix_webchat_voice_sessions_handoff_request_id")
        batch.drop_constraint("fk_voice_session_handoff_request", type_="foreignkey")
        batch.drop_column("wrap_up_expires_at")
        batch.drop_column("called_number")
        batch.drop_column("caller_number_hash")
        batch.drop_column("provider_call_id")
        batch.drop_column("direction")
        batch.drop_column("handoff_request_id")

    with op.batch_alter_table("operator_agent_states") as batch:
        batch.drop_constraint("ck_operator_agent_states_voice_wrap_up", type_="check")
        batch.drop_constraint("ck_operator_agent_states_voice_capacity", type_="check")
        batch.drop_column("voice_wrap_up_seconds")
        batch.drop_column("max_concurrent_voice_calls")
