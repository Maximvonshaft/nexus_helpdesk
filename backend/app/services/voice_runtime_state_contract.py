from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import User
from ..utils.time import utc_now
from ..voice_models import WebchatVoiceSession

_INSTALLED = False


def apply_session_compliance_state(
    db: Session,
    *,
    session: WebchatVoiceSession,
    recording_policy: str,
    transcription_policy: str,
) -> None:
    """Project policy authorization without claiming Provider execution."""

    from .voice_compliance_service import capability_authorized

    recording_allowed = capability_authorized(
        db,
        session=session,
        capability="recording",
        policy=recording_policy,
    )
    transcript_allowed = capability_authorized(
        db,
        session=session,
        capability="transcript_persistence",
        policy=transcription_policy,
    )
    session.recording_status = (
        "authorized"
        if recording_allowed
        else (
            "disabled"
            if recording_policy == "disabled"
            else (
                "notice_required"
                if recording_policy == "notice"
                else "consent_required"
            )
        )
    )
    session.transcript_status = (
        "authorized"
        if transcript_allowed
        else (
            "disabled"
            if transcription_policy == "disabled"
            else (
                "notice_required"
                if transcription_policy == "notice"
                else "consent_required"
            )
        )
    )
    session.updated_at = utc_now()
    db.flush()


def ensure_recording_command(
    db: Session,
    *,
    session: WebchatVoiceSession,
    actor: User | None = None,
) -> None:
    """Move authorized recording to start_requested only after command persistence."""

    from . import voice_room_control_service as room

    if session.recording_status not in {"authorized", "requested"}:
        return
    # Reuse the established policy and command writer while preserving a final
    # state that distinguishes authorization from an actual Provider request.
    session.recording_status = "requested"
    room._ORIGINAL_ENSURE_RECORDING_COMMAND(
        db,
        session=session,
        actor=actor,
    )
    if session.recording_status == "requested":
        session.recording_status = "start_requested"
        session.updated_at = utc_now()
        db.flush()


def _record_segment(
    db: Session,
    *,
    session: WebchatVoiceSession,
    segment_id: str,
    speaker_type: str,
    participant_identity: str,
    text: str,
    language: str | None,
) -> None:
    """A final Provider transcript segment is the first proof of active persistence."""

    from . import livekit_agent_turn_service as livekit_turn

    if session.transcript_status in {"authorized", "start_requested"}:
        session.transcript_status = "active"
        session.updated_at = utc_now()
        db.flush()
    livekit_turn._ORIGINAL_RECORD_SEGMENT(
        db,
        session=session,
        segment_id=segment_id,
        speaker_type=speaker_type,
        participant_identity=participant_identity,
        text=text,
        language=language,
    )


def install_voice_runtime_state_contract() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from . import livekit_agent_turn_service as livekit_turn
    from . import telephony_event_service as telephony_events
    from . import voice_compliance_service as compliance
    from . import voice_room_control_service as room

    if not hasattr(room, "_ORIGINAL_ENSURE_RECORDING_COMMAND"):
        room._ORIGINAL_ENSURE_RECORDING_COMMAND = room.ensure_recording_command
    if not hasattr(livekit_turn, "_ORIGINAL_RECORD_SEGMENT"):
        livekit_turn._ORIGINAL_RECORD_SEGMENT = livekit_turn._record_segment

    compliance.apply_session_compliance_state = apply_session_compliance_state
    telephony_events.apply_session_compliance_state = apply_session_compliance_state
    room.ensure_recording_command = ensure_recording_command
    telephony_events.ensure_recording_command = ensure_recording_command
    livekit_turn._record_segment = _record_segment
    _INSTALLED = True


__all__ = ["install_voice_runtime_state_contract"]
