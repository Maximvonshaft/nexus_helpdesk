from __future__ import annotations

from sqlalchemy import event

from ..voice_models import WebchatVoiceSession
from .voice_compliance_state import (
    VoiceComplianceState,
    normalize_voice_compliance_state,
)

_INSTALLED = False
_CAPTURE_IN_FLIGHT = {
    VoiceComplianceState.START_REQUESTED.value,
    VoiceComplianceState.ACTIVE.value,
    VoiceComplianceState.STOP_REQUESTED.value,
}
_SUCCESS_TERMINAL_SESSION_STATUSES = {"ended"}
_FAILURE_TERMINAL_SESSION_STATUSES = {"missed", "failed", "cancelled"}


def _set_insert_defaults(target: WebchatVoiceSession) -> None:
    # SQLAlchemy mapper events run before Python-side Column defaults are applied.
    # Persist the model's declared default explicitly so the Guard validates the
    # same state that the database will store, rather than rejecting a new row's
    # transient None value.
    if target.recording_status is None:
        target.recording_status = VoiceComplianceState.DISABLED.value
    if target.transcript_status is None:
        target.transcript_status = VoiceComplianceState.DISABLED.value


def _close_capture_states(target: WebchatVoiceSession) -> None:
    session_status = str(target.status or "").strip().lower()
    if session_status in _SUCCESS_TERMINAL_SESSION_STATUSES:
        terminal_state = VoiceComplianceState.COMPLETED.value
    elif session_status in _FAILURE_TERMINAL_SESSION_STATUSES:
        terminal_state = VoiceComplianceState.FAILED.value
    else:
        return
    if target.recording_status in _CAPTURE_IN_FLIGHT:
        target.recording_status = terminal_state
    if target.transcript_status in _CAPTURE_IN_FLIGHT:
        target.transcript_status = terminal_state


def _validate_session_states(target: WebchatVoiceSession) -> None:
    normalize_voice_compliance_state(target.recording_status)
    normalize_voice_compliance_state(target.transcript_status)


def _before_insert(
    _mapper,
    _connection,
    target: WebchatVoiceSession,
) -> None:
    _set_insert_defaults(target)
    _close_capture_states(target)
    _validate_session_states(target)


def _before_update(
    _mapper,
    _connection,
    target: WebchatVoiceSession,
) -> None:
    _close_capture_states(target)
    _validate_session_states(target)


def install_voice_compliance_state_events() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    event.listen(
        WebchatVoiceSession,
        "before_insert",
        _before_insert,
    )
    event.listen(
        WebchatVoiceSession,
        "before_update",
        _before_update,
    )
    _INSTALLED = True


__all__ = ["install_voice_compliance_state_events"]
