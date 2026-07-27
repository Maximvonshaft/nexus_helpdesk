"""Enforce one fail-closed Voice compliance state vocabulary.

Revision ID: 20260727_r4p1c
Revises: 20260727_r4p1b
Create Date: 2026-07-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260727_r4p1c"
down_revision = "20260727_r4p1b"
branch_labels = None
depends_on = None

_STATES = (
    "disabled",
    "policy_required",
    "notice_required",
    "consent_required",
    "authorized",
    "start_requested",
    "active",
    "stop_requested",
    "completed",
    "failed",
)
_STATE_SQL = ",".join(f"'{value}'" for value in _STATES)


def _normalize(bind) -> None:
    bind.execute(
        sa.text(
            "UPDATE webchat_voice_sessions SET recording_status = CASE "
            "WHEN recording_status IN ('requested','starting') THEN 'start_requested' "
            "WHEN recording_status = 'stopping' THEN 'stop_requested' "
            "WHEN recording_status IN ('stopped','ended') THEN 'completed' "
            "WHEN recording_status IN ('unavailable','unknown','') THEN 'policy_required' "
            "WHEN recording_status IS NULL THEN 'policy_required' "
            "ELSE recording_status END"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE webchat_voice_sessions SET transcript_status = CASE "
            "WHEN transcript_status IN ('requested','ready') THEN 'authorized' "
            "WHEN transcript_status = 'starting' THEN 'active' "
            "WHEN transcript_status IN ('stopped','ended') THEN 'completed' "
            "WHEN transcript_status IN ('unavailable','unknown','') THEN 'policy_required' "
            "WHEN transcript_status IS NULL THEN 'policy_required' "
            "ELSE transcript_status END"
        )
    )
    invalid_recording = int(
        bind.execute(
            sa.text(
                f"SELECT count(*) FROM webchat_voice_sessions "
                f"WHERE recording_status NOT IN ({_STATE_SQL})"
            )
        ).scalar()
        or 0
    )
    invalid_transcript = int(
        bind.execute(
            sa.text(
                f"SELECT count(*) FROM webchat_voice_sessions "
                f"WHERE transcript_status NOT IN ({_STATE_SQL})"
            )
        ).scalar()
        or 0
    )
    if invalid_recording or invalid_transcript:
        raise RuntimeError(
            "voice_compliance_state_requires_explicit_remediation:"
            f"recording={invalid_recording}:transcript={invalid_transcript}"
        )


def upgrade() -> None:
    bind = op.get_bind()
    _normalize(bind)
    with op.batch_alter_table("webchat_voice_sessions") as batch:
        batch.create_check_constraint(
            "ck_webchat_voice_sessions_recording_compliance_state",
            f"recording_status IN ({_STATE_SQL})",
        )
        batch.create_check_constraint(
            "ck_webchat_voice_sessions_transcript_compliance_state",
            f"transcript_status IN ({_STATE_SQL})",
        )


def downgrade() -> None:
    with op.batch_alter_table("webchat_voice_sessions") as batch:
        batch.drop_constraint(
            "ck_webchat_voice_sessions_transcript_compliance_state",
            type_="check",
        )
        batch.drop_constraint(
            "ck_webchat_voice_sessions_recording_compliance_state",
            type_="check",
        )
