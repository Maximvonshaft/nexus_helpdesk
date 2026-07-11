from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

PROFILE_SCHEMA_VERSION = "nexus_osr_release_profile_v1"
_REASON_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,119}$")


class CapabilityMode(StrEnum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    FORBIDDEN = "forbidden"


class CapabilityStatus(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"
    NOT_READY = "not_ready"
    NOT_CONFIGURED = "not_configured"


class ReleaseProfileName(StrEnum):
    DEVELOPMENT = "development"
    SHADOW = "shadow"
    PILOT = "pilot"
    FULL_OSR = "full_osr"


@dataclass(frozen=True)
class ReleaseProfile:
    name: ReleaseProfileName
    version: int
    capabilities: Mapping[str, CapabilityMode]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PROFILE_SCHEMA_VERSION,
            "name": self.name.value,
            "version": self.version,
            "capabilities": {
                key: self.capabilities[key].value
                for key in sorted(self.capabilities)
            },
        }


@dataclass(frozen=True)
class CapabilityEvidence:
    status: CapabilityStatus
    reason: str
    observed_at: str | None = None
    details: Mapping[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status.value,
            "reason": safe_reason(self.reason),
        }
        if self.observed_at:
            payload["observed_at"] = str(self.observed_at)[:64]
        if self.details:
            payload["details"] = bounded_safe_details(self.details)
        return payload


@dataclass(frozen=True)
class ProfileEvaluation:
    profile: ReleaseProfile
    status: CapabilityStatus
    reasons: tuple[str, ...]
    capabilities: Mapping[str, Mapping[str, Any]]
    configuration_hash: str

    @property
    def ready(self) -> bool:
        return self.status == CapabilityStatus.READY

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PROFILE_SCHEMA_VERSION,
            "profile": self.profile.as_dict(),
            "status": self.status.value,
            "ready": self.ready,
            "reasons": list(self.reasons),
            "capabilities": {
                key: dict(self.capabilities[key])
                for key in sorted(self.capabilities)
            },
            "configuration_hash": self.configuration_hash,
        }


_BASE_CAPABILITIES = {
    "database",
    "migration_identity",
    "storage",
    "runtime_contract_signing",
    "tenant_binding",
    "tracking_truth",
    "knowledge_runtime",
    "configured_escalation",
    "workers",
    "background_queue",
    "provider_runtime",
    "dispatch_outbox",
    "external_writes",
    "observability",
}


def _profile(name: ReleaseProfileName, *, required: set[str], forbidden: set[str] | None = None) -> ReleaseProfile:
    forbidden = forbidden or set()
    if required & forbidden:
        raise ValueError("release_profile_conflicting_capability")
    unknown = (required | forbidden) - _BASE_CAPABILITIES
    if unknown:
        raise ValueError("release_profile_unknown_capability")
    capabilities = {
        key: (
            CapabilityMode.REQUIRED
            if key in required
            else CapabilityMode.FORBIDDEN
            if key in forbidden
            else CapabilityMode.OPTIONAL
        )
        for key in sorted(_BASE_CAPABILITIES)
    }
    return ReleaseProfile(name=name, version=1, capabilities=capabilities)


PROFILES: Mapping[ReleaseProfileName, ReleaseProfile] = {
    ReleaseProfileName.DEVELOPMENT: _profile(
        ReleaseProfileName.DEVELOPMENT,
        required={"database"},
        forbidden={"external_writes"},
    ),
    ReleaseProfileName.SHADOW: _profile(
        ReleaseProfileName.SHADOW,
        required={
            "database",
            "migration_identity",
            "storage",
            "runtime_contract_signing",
            "tenant_binding",
            "tracking_truth",
            "knowledge_runtime",
            "configured_escalation",
            "workers",
            "background_queue",
            "observability",
        },
        forbidden={"external_writes"},
    ),
    ReleaseProfileName.PILOT: _profile(
        ReleaseProfileName.PILOT,
        required={
            "database",
            "migration_identity",
            "storage",
            "runtime_contract_signing",
            "tenant_binding",
            "tracking_truth",
            "knowledge_runtime",
            "configured_escalation",
            "workers",
            "background_queue",
            "provider_runtime",
            "dispatch_outbox",
            "observability",
        },
    ),
    ReleaseProfileName.FULL_OSR: _profile(
        ReleaseProfileName.FULL_OSR,
        required=set(_BASE_CAPABILITIES),
    ),
}


def safe_reason(value: Any, *, fallback: str = "unknown") -> str:
    normalized = str(value or "").strip().lower().replace(" ", "_")[:120]
    sensitive_tokens = ("bearer", "secret", "token", "password", "authorization", "credential", "cookie", "api_key", "apikey")
    if any(token in normalized for token in sensitive_tokens):
        return fallback
    return normalized if _REASON_RE.fullmatch(normalized) else fallback


def bounded_safe_details(value: Mapping[str, Any], *, depth: int = 0) -> dict[str, Any]:
    if depth > 3:
        return {"truncated": True}
    safe: dict[str, Any] = {}
    for raw_key, raw_value in list(value.items())[:32]:
        key = safe_reason(raw_key, fallback="field")[:80]
        if any(token in key for token in ("secret", "token", "password", "authorization", "credential", "cookie", "payload")):
            safe[key] = "[redacted]"
        elif raw_value is None or isinstance(raw_value, (bool, int, float)):
            safe[key] = raw_value
        elif isinstance(raw_value, str):
            safe[key] = raw_value[:160]
        elif isinstance(raw_value, Mapping):
            safe[key] = bounded_safe_details(raw_value, depth=depth + 1)
        elif isinstance(raw_value, (list, tuple)):
            safe[key] = [
                item if item is None or isinstance(item, (bool, int, float)) else str(item)[:120]
                for item in list(raw_value)[:16]
            ]
        else:
            safe[key] = type(raw_value).__name__
    return safe


def get_release_profile(value: str | ReleaseProfileName | None) -> ReleaseProfile:
    normalized = str(value or ReleaseProfileName.DEVELOPMENT.value).strip().lower()
    try:
        return PROFILES[ReleaseProfileName(normalized)]
    except (KeyError, ValueError) as exc:
        raise ValueError("release_profile_unknown") from exc


def safe_configuration_hash(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        bounded_safe_details(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def evaluate_release_profile(
    profile: ReleaseProfile,
    evidence: Mapping[str, CapabilityEvidence],
    *,
    configuration: Mapping[str, Any] | None = None,
) -> ProfileEvaluation:
    capability_results: dict[str, dict[str, Any]] = {}
    blocking_reasons: list[str] = []
    degraded_reasons: list[str] = []

    for capability, mode in profile.capabilities.items():
        observed = evidence.get(
            capability,
            CapabilityEvidence(
                status=CapabilityStatus.NOT_CONFIGURED,
                reason=f"{capability}.not_reported",
            ),
        )
        status = observed.status
        reason = safe_reason(observed.reason, fallback=f"{capability}.unknown")
        result = {
            "mode": mode.value,
            **observed.as_dict(),
        }
        capability_results[capability] = result

        if mode == CapabilityMode.REQUIRED:
            if status in {CapabilityStatus.NOT_READY, CapabilityStatus.NOT_CONFIGURED}:
                blocking_reasons.append(reason)
            elif status == CapabilityStatus.DEGRADED:
                degraded_reasons.append(reason)
        elif mode == CapabilityMode.OPTIONAL:
            if status in {CapabilityStatus.NOT_READY, CapabilityStatus.DEGRADED}:
                degraded_reasons.append(reason)
        elif mode == CapabilityMode.FORBIDDEN:
            if status in {CapabilityStatus.READY, CapabilityStatus.DEGRADED}:
                blocking_reasons.append(f"{capability}.forbidden_but_enabled")

    if blocking_reasons:
        overall = CapabilityStatus.NOT_READY
        reasons = tuple(dict.fromkeys(blocking_reasons + degraded_reasons))[:50]
    elif degraded_reasons:
        overall = CapabilityStatus.DEGRADED
        reasons = tuple(dict.fromkeys(degraded_reasons))[:50]
    else:
        overall = CapabilityStatus.READY
        reasons = ()

    config_payload = {
        "profile": profile.as_dict(),
        "effective": bounded_safe_details(configuration or {}),
    }
    return ProfileEvaluation(
        profile=profile,
        status=overall,
        reasons=reasons,
        capabilities=capability_results,
        configuration_hash=safe_configuration_hash(config_payload),
    )
