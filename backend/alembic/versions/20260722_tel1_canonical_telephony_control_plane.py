"""Canonical LiveKit telephony and voice-routing control plane.

Revision ID: 20260722_tel1
Revises: 20260721_0073
Create Date: 2026-07-22
"""

from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "20260722_tel1"
down_revision = "20260721_0073"
branch_labels = None
depends_on = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _backfill_voice_handoff_authority() -> None:
    """Move historical voice ownership into the canonical Handoff authority."""

    bind = op.get_bind()
    metadata = sa.MetaData()
    sessions = sa.Table("webchat_voice_sessions", metadata, autoload_with=bind)
    conversations = sa.Table("webchat_conversations", metadata, autoload_with=bind)
    handoffs = sa.Table("webchat_handoff_requests", metadata, autoload_with=bind)

    rows = bind.execute(
        sa.select(
            sessions.c.id,
            sessions.c.conversation_id,
            sessions.c.ticket_id,
            sessions.c.accepted_by_user_id,
            sessions.c.accepted_at,
            sessions.c.created_at,
        ).where(sessions.c.accepted_by_user_id.is_not(None))
    ).mappings()
    for row in rows:
        existing = bind.execute(
            sa.select(handoffs.c.id, handoffs.c.status).where(
                handoffs.c.conversation_id == row["conversation_id"],
                handoffs.c.status.in_(("requested", "accepted")),
            ).order_by(handoffs.c.id.desc()).limit(1)
        ).mappings().first()
        now = _utc_now()
        accepted_at = row["accepted_at"] or row["created_at"] or now
        if existing is None:
            inserted_id = bind.execute(
                sa.insert(handoffs)
                .values(
                    conversation_id=row["conversation_id"],
                    ticket_id=row["ticket_id"],
                    source="voice_migration",
                    trigger_type="voice_existing_ownership",
                    status="accepted",
                    reason_code="canonical_voice_ownership_migration",
                    requested_by_actor_type="system",
                    accepted_by_user_id=row["accepted_by_user_id"],
                    assigned_agent_id=row["accepted_by_user_id"],
                    requested_at=accepted_at,
                    accepted_at=accepted_at,
                    lock_version=1,
                    created_at=accepted_at,
                    updated_at=now,
                )
                .returning(handoffs.c.id)
            ).scalar_one()
        else:
            inserted_id = existing["id"]
            bind.execute(
                sa.update(handoffs)
                .where(handoffs.c.id == inserted_id)
                .values(
                    status="accepted",
                    accepted_by_user_id=row["accepted_by_user_id"],
                    assigned_agent_id=row["accepted_by_user_id"],
                    accepted_at=accepted_at,
                    updated_at=now,
                )
            )
        bind.execute(
            sa.update(sessions)
            .where(sessions.c.id == row["id"])
            .values(handoff_request_id=inserted_id)
        )
        bind.execute(
            sa.update(conversations)
            .where(conversations.c.id == row["conversation_id"])
            .values(
                current_handoff_request_id=inserted_id,
                handoff_status="accepted",
                active_agent_id=row["accepted_by_user_id"],
                ai_suspended=True,
                ai_suspended_reason="handoff_accepted",
                updated_at=now,
            )
        )


def upgrade() -> None:
    with op.batch_alter_table("operator_agent_states") as batch:
        batch.add_column(
            sa.Column("max_concurrent_voice_calls", sa.Integer(), nullable=False, server_default="1")
        )
        batch.add_column(
            sa.Column("voice_wrap_up_seconds", sa.Integer(), nullable=False, server_default="30")
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
        batch.add_column(sa.Column("channel_account_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("handoff_request_id", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("direction", sa.String(length=16), nullable=False, server_default="inbound")
        )
        batch.add_column(sa.Column("provider_call_id", sa.String(length=160), nullable=True))
        batch.add_column(sa.Column("caller_number_hash", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("called_number", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("recording_provider_ref", sa.String(length=180), nullable=True))
        batch.add_column(sa.Column("wrap_up_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key(
            "fk_voice_session_channel_account",
            "channel_accounts",
            ["channel_account_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_voice_session_handoff_request",
            "webchat_handoff_requests",
            ["handoff_request_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_check_constraint(
            "ck_webchat_voice_session_direction",
            "direction IN ('inbound', 'outbound')",
        )
        batch.create_index("ix_webchat_voice_sessions_channel_account_id", ["channel_account_id"])
        batch.create_index("ix_webchat_voice_sessions_handoff_request_id", ["handoff_request_id"])
        batch.create_index("ix_webchat_voice_sessions_provider_call_id", ["provider_call_id"])
        batch.create_index("ix_webchat_voice_sessions_caller_number_hash", ["caller_number_hash"])
        batch.create_index("ix_webchat_voice_sessions_called_number", ["called_number"])
        batch.create_index("ix_webchat_voice_sessions_recording_provider_ref", ["recording_provider_ref"])
        batch.create_index("ix_webchat_voice_sessions_wrap_up_expires_at", ["wrap_up_expires_at"])

    _backfill_voice_handoff_authority()

    with op.batch_alter_table("webchat_voice_sessions") as batch:
        batch.drop_index("ix_voice_accepted_by_user_id")
        batch.drop_column("accepted_by_user_id")

    with op.batch_alter_table("webchat_voice_participants") as batch:
        batch.add_column(sa.Column("parent_leg_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("provider_call_id", sa.String(length=160), nullable=True))
        batch.add_column(
            sa.Column("direction", sa.String(length=16), nullable=False, server_default="internal")
        )
        batch.add_column(sa.Column("termination_reason", sa.String(length=160), nullable=True))
        batch.add_column(sa.Column("metadata_json", sa.Text(), nullable=True))
        batch.add_column(sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            )
        )
        batch.create_foreign_key(
            "fk_voice_call_leg_parent",
            "webchat_voice_participants",
            ["parent_leg_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_check_constraint(
            "ck_voice_call_leg_direction",
            "direction IN ('inbound', 'outbound', 'internal')",
        )
        batch.create_index("ix_voice_call_leg_parent_leg_id", ["parent_leg_id"])
        batch.create_index("ix_voice_call_leg_provider_call_id", ["provider_call_id"])
        batch.create_index("ix_voice_call_leg_direction", ["direction"])
        batch.create_index("ix_voice_call_leg_started_at", ["started_at"])
        batch.create_index("ix_voice_call_leg_answered_at", ["answered_at"])
        batch.create_index("ix_voice_call_leg_ended_at", ["ended_at"])
        batch.create_index("ix_voice_call_leg_session_type", ["voice_session_id", "participant_type"])

    op.create_table(
        "voice_routing_offers",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("public_id", sa.String(length=64), nullable=False),
        sa.Column(
            "voice_session_id",
            sa.Integer(),
            sa.ForeignKey("webchat_voice_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "handoff_request_id",
            sa.Integer(),
            sa.ForeignKey("webchat_handoff_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="offered"),
        sa.Column(
            "offered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("declined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decline_reason", sa.String(length=240), nullable=True),
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
        sa.UniqueConstraint("public_id", name="uq_voice_routing_offer_public_id"),
        sa.UniqueConstraint(
            "voice_session_id",
            "agent_id",
            "sequence",
            name="uq_voice_offer_session_agent_sequence",
        ),
        sa.CheckConstraint(
            "status IN ('offered', 'accepted', 'declined', 'expired', 'cancelled')",
            name="ck_voice_routing_offer_status",
        ),
    )
    op.create_index(
        "ix_voice_offer_agent_status_expiry",
        "voice_routing_offers",
        ["agent_id", "status", "expires_at"],
    )
    op.create_index(
        "ix_voice_offer_session_status",
        "voice_routing_offers",
        ["voice_session_id", "status"],
    )
    op.create_index(
        "uq_voice_offer_active_session",
        "voice_routing_offers",
        ["voice_session_id"],
        unique=True,
        postgresql_where=sa.text("status = 'offered'"),
        sqlite_where=sa.text("status = 'offered'"),
    )

    with op.batch_alter_table("webchat_voice_session_actions") as batch:
        batch.add_column(sa.Column("public_id", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("idempotency_key", sa.String(length=160), nullable=True))
        batch.add_column(sa.Column("provider_reference", sa.String(length=180), nullable=True))
        batch.add_column(sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("lease_owner", sa.String(length=120), nullable=True))
        batch.add_column(sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("result_json", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            )
        )

    bind = op.get_bind()
    actions = sa.table(
        "webchat_voice_session_actions",
        sa.column("id", sa.Integer()),
        sa.column("public_id", sa.String()),
        sa.column("idempotency_key", sa.String()),
        sa.column("status", sa.String()),
        sa.column("provider_status", sa.String()),
        sa.column("provider_reason", sa.String()),
    )
    rows = bind.execute(sa.select(actions.c.id)).scalars().all()
    for action_id in rows:
        bind.execute(
            sa.update(actions)
            .where(actions.c.id == action_id)
            .values(
                public_id=f"vc_{action_id}",
                idempotency_key=f"historical-command-{action_id}",
                status="failed",
                provider_status="failed",
                provider_reason="historical_command_not_dispatched",
            )
        )

    with op.batch_alter_table("webchat_voice_session_actions") as batch:
        batch.alter_column("public_id", existing_type=sa.String(length=64), nullable=False)
        batch.alter_column("idempotency_key", existing_type=sa.String(length=160), nullable=False)
        batch.alter_column("actor_user_id", existing_type=sa.Integer(), nullable=True)
        batch.alter_column(
            "status",
            existing_type=sa.String(length=40),
            server_default="requested",
        )
        batch.alter_column(
            "provider_status",
            existing_type=sa.String(length=40),
            server_default="pending",
        )
        batch.alter_column(
            "provider_reason",
            existing_type=sa.String(length=160),
            nullable=True,
            server_default=None,
        )
        batch.create_unique_constraint("uq_voice_command_public_id", ["public_id"])
        batch.create_unique_constraint("uq_voice_session_action_idempotency_key", ["idempotency_key"])
        batch.create_check_constraint(
            "ck_voice_command_status",
            "status IN ('requested', 'dispatching', 'succeeded', 'failed', 'retryable', 'cancelled')",
        )
        batch.create_index("ix_voice_session_actions_status_created", ["status", "created_at"])
        batch.create_index(
            "ix_voice_command_dispatch",
            ["status", "next_attempt_at", "lease_expires_at"],
        )
        batch.create_index("ix_voice_command_provider_reference", ["provider_reference"])
        batch.create_index("ix_voice_command_next_attempt_at", ["next_attempt_at"])
        batch.create_index("ix_voice_command_lease_owner", ["lease_owner"])
        batch.create_index("ix_voice_command_lease_expires_at", ["lease_expires_at"])

    op.create_table(
        "voice_channel_configurations",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "channel_account_id",
            sa.Integer(),
            sa.ForeignKey("channel_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("livekit_project_ref", sa.String(length=160), nullable=True),
        sa.Column("inbound_trunk_id", sa.String(length=160), nullable=True),
        sa.Column("outbound_trunk_id", sa.String(length=160), nullable=True),
        sa.Column("dispatch_rule_id", sa.String(length=160), nullable=True),
        sa.Column("routing_mode", sa.String(length=24), nullable=False, server_default="ai_first"),
        sa.Column("ai_agent_name", sa.String(length=160), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="UTC"),
        sa.Column("business_hours_json", sa.Text(), nullable=True),
        sa.Column("queue_timeout_seconds", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("offer_timeout_seconds", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("wrap_up_seconds", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("overflow_action", sa.String(length=24), nullable=False, server_default="ai"),
        sa.Column("voicemail_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("recording_policy", sa.String(length=32), nullable=False, server_default="disabled"),
        sa.Column("transcription_policy", sa.String(length=32), nullable=False, server_default="disabled"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
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
            "offer_timeout_seconds BETWEEN 5 AND 120",
            name="ck_voice_channel_configuration_offer_timeout",
        ),
        sa.CheckConstraint(
            "wrap_up_seconds BETWEEN 0 AND 900",
            name="ck_voice_channel_configuration_wrap_up",
        ),
        sa.CheckConstraint(
            "recording_policy IN ('disabled', 'consent_required', 'always')",
            name="ck_voice_channel_configuration_recording_policy",
        ),
        sa.CheckConstraint(
            "transcription_policy IN ('disabled', 'consent_required', 'always')",
            name="ck_voice_channel_configuration_transcription_policy",
        ),
        sa.CheckConstraint(
            "overflow_action IN ('ai', 'voicemail', 'disconnect')",
            name="ck_voice_channel_configuration_overflow_action",
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
    op.create_index(
        "ix_voice_channel_configurations_dispatch_rule_id",
        "voice_channel_configurations",
        ["dispatch_rule_id"],
    )

    op.create_table(
        "telephony_event_inbox",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("provider_event_id", sa.String(length=180), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("safe_payload_json", sa.Text(), nullable=False),
        sa.Column("raw_payload_object_key", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="received"),
        sa.Column(
            "tenant_id",
            sa.Integer(),
            sa.ForeignKey("tenants.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "channel_account_id",
            sa.Integer(),
            sa.ForeignKey("channel_accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "voice_session_id",
            sa.Integer(),
            sa.ForeignKey("webchat_voice_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error_code", sa.String(length=120), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "provider",
            "provider_event_id",
            name="uq_telephony_event_provider_identity",
        ),
        sa.CheckConstraint(
            "status IN ('received', 'processing', 'processed', 'ignored', 'retryable', 'failed', 'dead_letter')",
            name="ck_telephony_event_inbox_status",
        ),
    )
    op.create_index(
        "ix_telephony_event_inbox_status_received",
        "telephony_event_inbox",
        ["status", "received_at"],
    )
    op.create_index(
        "ix_telephony_event_retry",
        "telephony_event_inbox",
        ["status", "next_attempt_at"],
    )
    op.create_index("ix_telephony_event_inbox_tenant_id", "telephony_event_inbox", ["tenant_id"])
    op.create_index(
        "ix_telephony_event_inbox_channel_account_id",
        "telephony_event_inbox",
        ["channel_account_id"],
    )
    op.create_index(
        "ix_telephony_event_inbox_voice_session_id",
        "telephony_event_inbox",
        ["voice_session_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_telephony_event_inbox_voice_session_id", table_name="telephony_event_inbox")
    op.drop_index("ix_telephony_event_inbox_channel_account_id", table_name="telephony_event_inbox")
    op.drop_index("ix_telephony_event_inbox_tenant_id", table_name="telephony_event_inbox")
    op.drop_index("ix_telephony_event_retry", table_name="telephony_event_inbox")
    op.drop_index("ix_telephony_event_inbox_status_received", table_name="telephony_event_inbox")
    op.drop_table("telephony_event_inbox")

    op.drop_index("ix_voice_channel_configurations_dispatch_rule_id", table_name="voice_channel_configurations")
    op.drop_index("ix_voice_channel_configurations_inbound_trunk_id", table_name="voice_channel_configurations")
    op.drop_index("ix_voice_channel_configurations_enabled", table_name="voice_channel_configurations")
    op.drop_index("ix_voice_channel_configurations_channel_account_id", table_name="voice_channel_configurations")
    op.drop_table("voice_channel_configurations")

    with op.batch_alter_table("webchat_voice_session_actions") as batch:
        batch.drop_index("ix_voice_command_lease_expires_at")
        batch.drop_index("ix_voice_command_lease_owner")
        batch.drop_index("ix_voice_command_next_attempt_at")
        batch.drop_index("ix_voice_command_provider_reference")
        batch.drop_index("ix_voice_command_dispatch")
        batch.drop_index("ix_voice_session_actions_status_created")
        batch.drop_constraint("ck_voice_command_status", type_="check")
        batch.drop_constraint("uq_voice_session_action_idempotency_key", type_="unique")
        batch.drop_constraint("uq_voice_command_public_id", type_="unique")
        batch.alter_column("actor_user_id", existing_type=sa.Integer(), nullable=False)
        batch.alter_column("status", existing_type=sa.String(length=40), server_default="recorded")
        batch.alter_column("provider_status", existing_type=sa.String(length=40), server_default="pending")
        batch.alter_column(
            "provider_reason",
            existing_type=sa.String(length=160),
            nullable=False,
            server_default="not_started",
        )
        batch.drop_column("updated_at")
        batch.drop_column("result_json")
        batch.drop_column("completed_at")
        batch.drop_column("lease_expires_at")
        batch.drop_column("lease_owner")
        batch.drop_column("next_attempt_at")
        batch.drop_column("last_attempt_at")
        batch.drop_column("attempt_count")
        batch.drop_column("provider_reference")
        batch.drop_column("idempotency_key")
        batch.drop_column("public_id")

    op.drop_index("uq_voice_offer_active_session", table_name="voice_routing_offers")
    op.drop_index("ix_voice_offer_session_status", table_name="voice_routing_offers")
    op.drop_index("ix_voice_offer_agent_status_expiry", table_name="voice_routing_offers")
    op.drop_table("voice_routing_offers")

    with op.batch_alter_table("webchat_voice_participants") as batch:
        batch.drop_index("ix_voice_call_leg_session_type")
        batch.drop_index("ix_voice_call_leg_ended_at")
        batch.drop_index("ix_voice_call_leg_answered_at")
        batch.drop_index("ix_voice_call_leg_started_at")
        batch.drop_index("ix_voice_call_leg_direction")
        batch.drop_index("ix_voice_call_leg_provider_call_id")
        batch.drop_index("ix_voice_call_leg_parent_leg_id")
        batch.drop_constraint("ck_voice_call_leg_direction", type_="check")
        batch.drop_constraint("fk_voice_call_leg_parent", type_="foreignkey")
        batch.drop_column("updated_at")
        batch.drop_column("ended_at")
        batch.drop_column("answered_at")
        batch.drop_column("started_at")
        batch.drop_column("metadata_json")
        batch.drop_column("termination_reason")
        batch.drop_column("direction")
        batch.drop_column("provider_call_id")
        batch.drop_column("parent_leg_id")

    with op.batch_alter_table("webchat_voice_sessions") as batch:
        batch.add_column(sa.Column("accepted_by_user_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_webchat_voice_sessions_accepted_by_user_id",
            "users",
            ["accepted_by_user_id"],
            ["id"],
        )
        batch.create_index("ix_voice_accepted_by_user_id", ["accepted_by_user_id"])

    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE webchat_voice_sessions AS s
            SET accepted_by_user_id = h.assigned_agent_id
            FROM webchat_handoff_requests AS h
            WHERE s.handoff_request_id = h.id
              AND h.status = 'accepted'
            """
        )
    )

    with op.batch_alter_table("webchat_voice_sessions") as batch:
        batch.drop_index("ix_webchat_voice_sessions_wrap_up_expires_at")
        batch.drop_index("ix_webchat_voice_sessions_recording_provider_ref")
        batch.drop_index("ix_webchat_voice_sessions_called_number")
        batch.drop_index("ix_webchat_voice_sessions_caller_number_hash")
        batch.drop_index("ix_webchat_voice_sessions_provider_call_id")
        batch.drop_index("ix_webchat_voice_sessions_handoff_request_id")
        batch.drop_index("ix_webchat_voice_sessions_channel_account_id")
        batch.drop_constraint("ck_webchat_voice_session_direction", type_="check")
        batch.drop_constraint("fk_voice_session_handoff_request", type_="foreignkey")
        batch.drop_constraint("fk_voice_session_channel_account", type_="foreignkey")
        batch.drop_column("wrap_up_expires_at")
        batch.drop_column("recording_provider_ref")
        batch.drop_column("called_number")
        batch.drop_column("caller_number_hash")
        batch.drop_column("provider_call_id")
        batch.drop_column("direction")
        batch.drop_column("handoff_request_id")
        batch.drop_column("channel_account_id")

    with op.batch_alter_table("operator_agent_states") as batch:
        batch.drop_constraint("ck_operator_agent_states_voice_wrap_up", type_="check")
        batch.drop_constraint("ck_operator_agent_states_voice_capacity", type_="check")
        batch.drop_column("voice_wrap_up_seconds")
        batch.drop_column("max_concurrent_voice_calls")
