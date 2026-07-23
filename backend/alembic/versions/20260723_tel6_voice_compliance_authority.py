"""Canonical voice compliance evidence and policy authority.

Revision ID: 20260723_tel6
Revises: 20260722_tel5
Create Date: 2026-07-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260723_tel6"
down_revision = "20260722_tel5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE voice_channel_configurations "
            "SET recording_policy = 'disabled' "
            "WHERE recording_policy <> 'disabled'"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE voice_channel_configurations "
            "SET transcription_policy = 'disabled' "
            "WHERE transcription_policy <> 'disabled'"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE voice_channel_configurations "
            "SET overflow_action = 'disconnect' "
            "WHERE overflow_action = 'voicemail'"
        )
    )

    with op.batch_alter_table("voice_channel_configurations") as batch:
        batch.drop_constraint(
            "ck_voice_channel_configuration_recording_policy",
            type_="check",
        )
        batch.drop_constraint(
            "ck_voice_channel_configuration_transcription_policy",
            type_="check",
        )
        batch.drop_constraint(
            "ck_voice_channel_configuration_overflow_action",
            type_="check",
        )
        batch.create_check_constraint(
            "ck_voice_channel_configuration_recording_policy",
            "recording_policy IN ('disabled', 'notice', 'explicit_consent')",
        )
        batch.create_check_constraint(
            "ck_voice_channel_configuration_transcription_policy",
            "transcription_policy IN ('disabled', 'notice', 'explicit_consent')",
        )
        batch.create_check_constraint(
            "ck_voice_channel_configuration_overflow_action",
            "overflow_action IN ('ai', 'disconnect')",
        )
        batch.drop_column("voicemail_enabled")

    with op.batch_alter_table("webchat_voice_sessions") as batch:
        batch.drop_column("recording_consent")

    op.create_table(
        "voice_compliance_evidence",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("public_id", sa.String(length=64), nullable=False),
        sa.Column(
            "voice_session_id",
            sa.Integer(),
            sa.ForeignKey("webchat_voice_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("capability", sa.String(length=32), nullable=False),
        sa.Column("policy", sa.String(length=32), nullable=False),
        sa.Column("policy_version", sa.String(length=80), nullable=False),
        sa.Column("prompt_sha256", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("participant_identity_hash", sa.String(length=64), nullable=True),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("confirmation_public_id", sa.String(length=64), nullable=True),
        sa.Column("idempotency_key", sa.String(length=180), nullable=False),
        sa.Column(
            "evidence_at",
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
        sa.UniqueConstraint(
            "public_id",
            name="uq_voice_compliance_evidence_public_id",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_voice_compliance_evidence_idempotency",
        ),
        sa.CheckConstraint(
            "capability IN ('recording', 'transcript_persistence')",
            name="ck_voice_compliance_evidence_capability",
        ),
        sa.CheckConstraint(
            "policy IN ('disabled', 'notice', 'explicit_consent')",
            name="ck_voice_compliance_evidence_policy",
        ),
        sa.CheckConstraint(
            "source IN ('browser', 'sip_controller', 'migration')",
            name="ck_voice_compliance_evidence_source",
        ),
        sa.CheckConstraint(
            "decision IN ('notice_delivered', 'accepted', 'declined', 'timeout')",
            name="ck_voice_compliance_evidence_decision",
        ),
    )
    op.create_index(
        "ix_voice_compliance_evidence_public_id",
        "voice_compliance_evidence",
        ["public_id"],
        unique=True,
    )
    op.create_index(
        "ix_voice_compliance_evidence_voice_session_id",
        "voice_compliance_evidence",
        ["voice_session_id"],
    )
    op.create_index(
        "ix_voice_compliance_evidence_capability",
        "voice_compliance_evidence",
        ["capability"],
    )
    op.create_index(
        "ix_voice_compliance_evidence_policy",
        "voice_compliance_evidence",
        ["policy"],
    )
    op.create_index(
        "ix_voice_compliance_evidence_policy_version",
        "voice_compliance_evidence",
        ["policy_version"],
    )
    op.create_index(
        "ix_voice_compliance_evidence_prompt_sha256",
        "voice_compliance_evidence",
        ["prompt_sha256"],
    )
    op.create_index(
        "ix_voice_compliance_evidence_source",
        "voice_compliance_evidence",
        ["source"],
    )
    op.create_index(
        "ix_voice_compliance_evidence_participant_identity_hash",
        "voice_compliance_evidence",
        ["participant_identity_hash"],
    )
    op.create_index(
        "ix_voice_compliance_evidence_decision",
        "voice_compliance_evidence",
        ["decision"],
    )
    op.create_index(
        "ix_voice_compliance_evidence_confirmation_public_id",
        "voice_compliance_evidence",
        ["confirmation_public_id"],
    )
    op.create_index(
        "ix_voice_compliance_evidence_idempotency_key",
        "voice_compliance_evidence",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index(
        "ix_voice_compliance_evidence_evidence_at",
        "voice_compliance_evidence",
        ["evidence_at"],
    )
    op.create_index(
        "ix_voice_compliance_evidence_created_at",
        "voice_compliance_evidence",
        ["created_at"],
    )
    op.create_index(
        "ix_voice_compliance_session_capability_time",
        "voice_compliance_evidence",
        ["voice_session_id", "capability", "evidence_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_voice_compliance_session_capability_time",
        table_name="voice_compliance_evidence",
    )
    op.drop_index(
        "ix_voice_compliance_evidence_created_at",
        table_name="voice_compliance_evidence",
    )
    op.drop_index(
        "ix_voice_compliance_evidence_evidence_at",
        table_name="voice_compliance_evidence",
    )
    op.drop_index(
        "ix_voice_compliance_evidence_idempotency_key",
        table_name="voice_compliance_evidence",
    )
    op.drop_index(
        "ix_voice_compliance_evidence_confirmation_public_id",
        table_name="voice_compliance_evidence",
    )
    op.drop_index(
        "ix_voice_compliance_evidence_decision",
        table_name="voice_compliance_evidence",
    )
    op.drop_index(
        "ix_voice_compliance_evidence_participant_identity_hash",
        table_name="voice_compliance_evidence",
    )
    op.drop_index(
        "ix_voice_compliance_evidence_source",
        table_name="voice_compliance_evidence",
    )
    op.drop_index(
        "ix_voice_compliance_evidence_prompt_sha256",
        table_name="voice_compliance_evidence",
    )
    op.drop_index(
        "ix_voice_compliance_evidence_policy_version",
        table_name="voice_compliance_evidence",
    )
    op.drop_index(
        "ix_voice_compliance_evidence_policy",
        table_name="voice_compliance_evidence",
    )
    op.drop_index(
        "ix_voice_compliance_evidence_capability",
        table_name="voice_compliance_evidence",
    )
    op.drop_index(
        "ix_voice_compliance_evidence_voice_session_id",
        table_name="voice_compliance_evidence",
    )
    op.drop_index(
        "ix_voice_compliance_evidence_public_id",
        table_name="voice_compliance_evidence",
    )
    op.drop_table("voice_compliance_evidence")

    with op.batch_alter_table("webchat_voice_sessions") as batch:
        batch.add_column(
            sa.Column(
                "recording_consent",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE voice_channel_configurations "
            "SET recording_policy = 'disabled' "
            "WHERE recording_policy <> 'disabled'"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE voice_channel_configurations "
            "SET transcription_policy = 'disabled' "
            "WHERE transcription_policy <> 'disabled'"
        )
    )
    with op.batch_alter_table("voice_channel_configurations") as batch:
        batch.add_column(
            sa.Column(
                "voicemail_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.drop_constraint(
            "ck_voice_channel_configuration_recording_policy",
            type_="check",
        )
        batch.drop_constraint(
            "ck_voice_channel_configuration_transcription_policy",
            type_="check",
        )
        batch.drop_constraint(
            "ck_voice_channel_configuration_overflow_action",
            type_="check",
        )
        batch.create_check_constraint(
            "ck_voice_channel_configuration_recording_policy",
            "recording_policy IN ('disabled', 'consent_required', 'always')",
        )
        batch.create_check_constraint(
            "ck_voice_channel_configuration_transcription_policy",
            "transcription_policy IN ('disabled', 'consent_required', 'always')",
        )
        batch.create_check_constraint(
            "ck_voice_channel_configuration_overflow_action",
            "overflow_action IN ('ai', 'voicemail', 'disconnect')",
        )
