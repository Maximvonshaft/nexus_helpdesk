from __future__ import annotations

from sqlalchemy import event

from ..voice_models import WebchatVoiceSession
from .voice_compliance_state import normalize_voice_compliance_state

_INSTALLED = False


def _validate_session_states(
    _mapper,
    _connection,
    target: WebchatVoiceSession,
) -> None:
    normalize_voice_compliance_state(target.recording_status)
    normalize_voice_compliance_state(target.transcript_status)


def install_voice_compliance_state_events() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    event.listen(
        WebchatVoiceSession,
        "before_insert",
        _validate_session_states,
    )
    event.listen(
        WebchatVoiceSession,
        "before_update",
        _validate_session_states,
    )
    _INSTALLED = True


__all__ = ["install_voice_compliance_state_events"]
