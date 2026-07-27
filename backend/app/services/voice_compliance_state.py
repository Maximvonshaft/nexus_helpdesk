from __future__ import annotations

from enum import StrEnum


class VoiceComplianceState(StrEnum):
    DISABLED = "disabled"
    POLICY_REQUIRED = "policy_required"
    NOTICE_REQUIRED = "notice_required"
    CONSENT_REQUIRED = "consent_required"
    AUTHORIZED = "authorized"
    START_REQUESTED = "start_requested"
    ACTIVE = "active"
    STOP_REQUESTED = "stop_requested"
    COMPLETED = "completed"
    FAILED = "failed"


VOICE_COMPLIANCE_STATES = frozenset(state.value for state in VoiceComplianceState)
VOICE_CAPTURE_ALLOWED_STATES = frozenset(
    {
        VoiceComplianceState.AUTHORIZED.value,
        VoiceComplianceState.START_REQUESTED.value,
        VoiceComplianceState.ACTIVE.value,
        VoiceComplianceState.STOP_REQUESTED.value,
        VoiceComplianceState.COMPLETED.value,
    }
)


def normalize_voice_compliance_state(value: object) -> VoiceComplianceState:
    try:
        return VoiceComplianceState(str(value or "").strip().lower())
    except ValueError as exc:
        raise RuntimeError("voice_compliance_state_invalid") from exc


__all__ = [
    "VOICE_CAPTURE_ALLOWED_STATES",
    "VOICE_COMPLIANCE_STATES",
    "VoiceComplianceState",
    "normalize_voice_compliance_state",
]
