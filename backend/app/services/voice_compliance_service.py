from __future__ import annotations

import hashlib
import secrets
from typing import Any

from sqlalchemy.orm import Session

from ..utils.time import utc_now
from ..voice_compliance_models import VoiceComplianceEvidence
from ..voice_models import WebchatVoiceSession

POLICY_VERSION = "nexus.voice-compliance.v1"
CAPABILITIES = {"recording", "transcript_persistence"}
POLICIES = {"disabled", "notice", "explicit_consent"}
SOURCES = {"browser", "sip_controller", "migration"}
DECISIONS = {"notice_delivered", "accepted", "declined", "timeout"}

_PROMPTS = {
    ("recording", "notice"): (
        "This call may be recorded for service quality and dispute resolution. "
        "You can continue without recording by declining before joining."
    ),
    ("recording", "explicit_consent"): (
        "May Nexus record this call for service quality and dispute resolution?"
    ),
    ("transcript_persistence", "notice"): (
        "A written transcript of this call may be stored with the support conversation. "
        "You can continue without transcript storage by declining before joining."
    ),
    ("transcript_persistence", "explicit_consent"): (
        "May Nexus store a written transcript of this call with the support conversation?"
    ),
}


def _clean(value: Any, *, limit: int) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def participant_identity_hash(value: str | None) -> str | None:
    normalized = _clean(value, limit=240)
    return _sha256(normalized) if normalized else None


def policy_prompt(capability: str, policy: str) -> dict[str, Any]:
    normalized_capability = _clean(capability, limit=32).lower()
    normalized_policy = _clean(policy, limit=32).lower()
    if normalized_capability not in CAPABILITIES:
        raise ValueError("voice_compliance_capability_invalid")
    if normalized_policy not in POLICIES:
        raise ValueError("voice_compliance_policy_invalid")
    prompt = _PROMPTS.get((normalized_capability, normalized_policy), "")
    return {
        "capability": normalized_capability,
        "policy": normalized_policy,
        "policy_version": POLICY_VERSION,
        "prompt": prompt or None,
        "prompt_sha256": _sha256(prompt),
        "decision_required": normalized_policy != "disabled",
    }


def policy_bundle(
    *,
    recording_policy: str,
    transcription_policy: str,
) -> dict[str, Any]:
    recording = policy_prompt("recording", recording_policy)
    transcription = policy_prompt(
        "transcript_persistence",
        transcription_policy,
    )
    return {
        "schema": "nexus.voice-compliance-policy.v1",
        "policy_version": POLICY_VERSION,
        "recording": recording,
        "transcript_persistence": transcription,
    }


def expected_authorizing_decision(policy: str) -> str | None:
    if policy == "notice":
        return "notice_delivered"
    if policy == "explicit_consent":
        return "accepted"
    return None


def _evidence_query(
    db: Session,
    *,
    session_id: int,
    capability: str,
):
    return db.query(VoiceComplianceEvidence).filter(
        VoiceComplianceEvidence.voice_session_id == session_id,
        VoiceComplianceEvidence.capability == capability,
    )


def latest_evidence(
    db: Session,
    *,
    session: WebchatVoiceSession,
    capability: str,
) -> VoiceComplianceEvidence | None:
    return (
        _evidence_query(
            db,
            session_id=session.id,
            capability=capability,
        )
        .order_by(
            VoiceComplianceEvidence.evidence_at.desc(),
            VoiceComplianceEvidence.id.desc(),
        )
        .first()
    )


def capability_authorized(
    db: Session,
    *,
    session: WebchatVoiceSession,
    capability: str,
    policy: str,
) -> bool:
    expected = expected_authorizing_decision(policy)
    if expected is None:
        return False
    row = latest_evidence(db, session=session, capability=capability)
    return bool(
        row is not None
        and row.policy == policy
        and row.policy_version == POLICY_VERSION
        and row.prompt_sha256 == policy_prompt(capability, policy)["prompt_sha256"]
        and row.decision == expected
    )


def record_evidence(
    db: Session,
    *,
    session: WebchatVoiceSession,
    capability: str,
    policy: str,
    policy_version: str,
    prompt_sha256: str,
    source: str,
    decision: str,
    participant_identity: str | None,
    idempotency_key: str,
    confirmation_public_id: str | None = None,
) -> VoiceComplianceEvidence:
    capability = _clean(capability, limit=32).lower()
    policy = _clean(policy, limit=32).lower()
    policy_version = _clean(policy_version, limit=80)
    prompt_sha256 = _clean(prompt_sha256, limit=64).lower()
    source = _clean(source, limit=32).lower()
    decision = _clean(decision, limit=32).lower()
    idempotency_key = _clean(idempotency_key, limit=180)
    confirmation_public_id = _clean(confirmation_public_id, limit=64) or None
    if capability not in CAPABILITIES:
        raise ValueError("voice_compliance_capability_invalid")
    if policy not in POLICIES:
        raise ValueError("voice_compliance_policy_invalid")
    if source not in SOURCES:
        raise ValueError("voice_compliance_source_invalid")
    if decision not in DECISIONS:
        raise ValueError("voice_compliance_decision_invalid")
    if not idempotency_key:
        raise ValueError("voice_compliance_idempotency_required")
    expected = policy_prompt(capability, policy)
    if policy_version != expected["policy_version"]:
        raise ValueError("voice_compliance_policy_version_mismatch")
    if prompt_sha256 != expected["prompt_sha256"]:
        raise ValueError("voice_compliance_prompt_mismatch")
    if policy == "disabled":
        raise ValueError("voice_compliance_evidence_not_allowed_for_disabled_policy")
    if policy == "notice" and decision not in {"notice_delivered", "declined", "timeout"}:
        raise ValueError("voice_compliance_notice_decision_invalid")
    if policy == "explicit_consent" and decision not in {"accepted", "declined", "timeout"}:
        raise ValueError("voice_compliance_consent_decision_invalid")

    existing = (
        db.query(VoiceComplianceEvidence)
        .filter(VoiceComplianceEvidence.idempotency_key == idempotency_key)
        .first()
    )
    if existing is not None:
        if (
            existing.voice_session_id != session.id
            or existing.capability != capability
            or existing.policy != policy
            or existing.policy_version != policy_version
            or existing.prompt_sha256 != prompt_sha256
            or existing.source != source
            or existing.decision != decision
        ):
            raise ValueError("voice_compliance_idempotency_payload_mismatch")
        return existing

    row = VoiceComplianceEvidence(
        public_id=f"vce_{secrets.token_urlsafe(18)}",
        voice_session_id=session.id,
        capability=capability,
        policy=policy,
        policy_version=policy_version,
        prompt_sha256=prompt_sha256,
        source=source,
        participant_identity_hash=participant_identity_hash(participant_identity),
        decision=decision,
        confirmation_public_id=confirmation_public_id,
        idempotency_key=idempotency_key,
        evidence_at=utc_now(),
        created_at=utc_now(),
    )
    db.add(row)
    db.flush()
    return row


def apply_session_compliance_state(
    db: Session,
    *,
    session: WebchatVoiceSession,
    recording_policy: str,
    transcription_policy: str,
) -> None:
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
        "requested"
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
        "active"
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


def evidence_projection(row: VoiceComplianceEvidence) -> dict[str, Any]:
    return {
        "id": row.public_id,
        "capability": row.capability,
        "policy": row.policy,
        "policy_version": row.policy_version,
        "prompt_sha256": row.prompt_sha256,
        "source": row.source,
        "decision": row.decision,
        "confirmation_id": row.confirmation_public_id,
        "evidence_at": row.evidence_at.isoformat() if row.evidence_at else None,
    }
