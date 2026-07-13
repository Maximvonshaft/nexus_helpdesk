from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any


SCHEMA_VERSION = "nexus.osr.release-profile.v1"
_MAX_EVIDENCE_ENTRIES = 64
_MAX_CONFIG_DEPTH = 4
_MAX_CONFIG_ENTRIES = 64
_MAX_CONFIG_LIST_ITEMS = 64
_MAX_CONFIG_STRING_LENGTH = 512
_MAX_CONFIG_KEY_LENGTH = 128
_REDACTED = "[redacted]"
_ACRONYM_BOUNDARY_RE = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_KEY_SEGMENT_RE = re.compile(r"[^a-z0-9]+")
_SENSITIVE_KEY_TERMINALS = frozenset(
    {
        "secret",
        "secrets",
        "password",
        "passwords",
        "authorization",
        "credential",
        "credentials",
        "cookie",
        "cookies",
        "payload",
        "token",
    }
)
_SENSITIVE_KEY_PAIRS = frozenset(
    {
        ("api", "key"),
        ("private", "key"),
        ("access", "key"),
        ("signing", "key"),
        ("secret", "key"),
    }
)
_NONSECRET_TOKEN_COUNT_PREFIXES = frozenset(
    {"max", "min", "input", "output", "total", "context", "usage", "budget"}
)


class ReleaseProfileContractError(ValueError):
    """Raised when a release-profile contract input is unsafe or invalid."""


class ProfileName(str, Enum):
    DEVELOPMENT = "development"
    SHADOW = "shadow"
    PILOT = "pilot"
    FULL_OSR = "full_osr"


class Requirement(str, Enum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    FORBIDDEN = "forbidden"


class CapabilityState(str, Enum):
    MISSING = "missing"
    DISABLED = "disabled"
    READY = "ready"
    DEGRADED = "degraded"
    FAILED = "failed"


class ReadinessStatus(str, Enum):
    READY = "ready"
    DEGRADED = "degraded"
    NOT_READY = "not_ready"


class Capability(str, Enum):
    DATABASE = "database"
    MIGRATION_IDENTITY = "migration_identity"
    STORAGE = "storage"
    RUNTIME_SIGNING = "runtime_signing"
    TENANT_AUTHORITY = "tenant_authority"
    TRACKING_TRUTH = "tracking_truth"
    KNOWLEDGE_READINESS = "knowledge_readiness"
    ESCALATION_POLICY = "escalation_policy"
    WORKER_HEARTBEAT = "worker_heartbeat"
    WORKER_PROGRESS = "worker_progress"
    QUEUE_HEALTH = "queue_health"
    PROVIDER_RUNTIME = "provider_runtime"
    PROVIDER_CANARY_AUTHORITY = "provider_canary_authority"
    DISPATCH_EXECUTION = "dispatch_execution"
    DISPATCH_ACKNOWLEDGEMENT = "dispatch_acknowledgement"
    EXTERNAL_WRITES = "external_writes"
    OBSERVABILITY = "observability"
    RECOVERY = "recovery"
    RESILIENCE = "resilience"
    AI_RUNTIME_CONTRACT = "ai_runtime_contract"
    RAG_V2 = "rag_v2"
    RAG_SYNC_FRESHNESS = "rag_sync_freshness"
    RUNTIME_DEPLOYMENT_IDENTITY = "runtime_deployment_identity"
    VOICE_RUNTIME = "voice_runtime"


@dataclass(frozen=True)
class ReleaseProfile:
    schema_version: str
    name: ProfileName
    capabilities: Mapping[Capability, Requirement]


@dataclass(frozen=True)
class ReleaseProfileEvaluation:
    schema_version: str
    profile: str
    status: ReadinessStatus
    reason_codes: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "profile": self.profile,
            "status": self.status.value,
            "reason_codes": list(self.reason_codes),
        }


def _profile(
    name: ProfileName,
    *,
    required: set[Capability],
    forbidden: set[Capability],
) -> ReleaseProfile:
    if required & forbidden:
        raise ReleaseProfileContractError("release_profile_requirement_conflict")
    declarations = {
        capability: (
            Requirement.REQUIRED
            if capability in required
            else Requirement.FORBIDDEN
            if capability in forbidden
            else Requirement.OPTIONAL
        )
        for capability in Capability
    }
    return ReleaseProfile(
        schema_version=SCHEMA_VERSION,
        name=name,
        capabilities=MappingProxyType(declarations),
    )


_DEVELOPMENT_REQUIRED = {
    Capability.DATABASE,
    Capability.MIGRATION_IDENTITY,
    Capability.STORAGE,
    Capability.RUNTIME_SIGNING,
}
_DEVELOPMENT_FORBIDDEN = {
    Capability.PROVIDER_RUNTIME,
    Capability.PROVIDER_CANARY_AUTHORITY,
    Capability.DISPATCH_EXECUTION,
    Capability.DISPATCH_ACKNOWLEDGEMENT,
    Capability.EXTERNAL_WRITES,
}

_SHADOW_REQUIRED = {
    Capability.DATABASE,
    Capability.MIGRATION_IDENTITY,
    Capability.STORAGE,
    Capability.RUNTIME_SIGNING,
    Capability.TRACKING_TRUTH,
    Capability.KNOWLEDGE_READINESS,
    Capability.ESCALATION_POLICY,
    Capability.WORKER_HEARTBEAT,
    Capability.WORKER_PROGRESS,
    Capability.QUEUE_HEALTH,
    Capability.PROVIDER_RUNTIME,
    Capability.PROVIDER_CANARY_AUTHORITY,
    Capability.OBSERVABILITY,
    Capability.AI_RUNTIME_CONTRACT,
    Capability.RAG_V2,
    Capability.RUNTIME_DEPLOYMENT_IDENTITY,
}
_SHADOW_FORBIDDEN = {
    Capability.DISPATCH_EXECUTION,
    Capability.DISPATCH_ACKNOWLEDGEMENT,
    Capability.EXTERNAL_WRITES,
}

_PILOT_REQUIRED = set(Capability) - {Capability.VOICE_RUNTIME}
_PILOT_FORBIDDEN: set[Capability] = set()
_FULL_OSR_REQUIRED = set(Capability)


_PROFILES: Mapping[ProfileName, ReleaseProfile] = MappingProxyType(
    {
        ProfileName.DEVELOPMENT: _profile(
            ProfileName.DEVELOPMENT,
            required=_DEVELOPMENT_REQUIRED,
            forbidden=_DEVELOPMENT_FORBIDDEN,
        ),
        ProfileName.SHADOW: _profile(
            ProfileName.SHADOW,
            required=_SHADOW_REQUIRED,
            forbidden=_SHADOW_FORBIDDEN,
        ),
        ProfileName.PILOT: _profile(
            ProfileName.PILOT,
            required=_PILOT_REQUIRED,
            forbidden=_PILOT_FORBIDDEN,
        ),
        ProfileName.FULL_OSR: _profile(
            ProfileName.FULL_OSR,
            required=_FULL_OSR_REQUIRED,
            forbidden=set(),
        ),
    }
)


def _validate_registry() -> None:
    if set(_PROFILES) != set(ProfileName):
        raise ReleaseProfileContractError("release_profile_registry_incomplete")
    expected_capabilities = set(Capability)
    for profile_name, profile in _PROFILES.items():
        if profile.schema_version != SCHEMA_VERSION or profile.name is not profile_name:
            raise ReleaseProfileContractError("release_profile_identity_invalid")
        if set(profile.capabilities) != expected_capabilities:
            raise ReleaseProfileContractError("release_profile_capabilities_incomplete")
        if any(not isinstance(value, Requirement) for value in profile.capabilities.values()):
            raise ReleaseProfileContractError("release_profile_requirement_invalid")


_validate_registry()


def _coerce_profile_name(value: ProfileName | str) -> ProfileName:
    if isinstance(value, ProfileName):
        return value
    try:
        return ProfileName(str(value))
    except (TypeError, ValueError) as exc:
        raise ReleaseProfileContractError("release_profile_unknown") from exc


def get_profile(value: ProfileName | str) -> ReleaseProfile:
    return _PROFILES[_coerce_profile_name(value)]


def _normalize_evidence(
    evidence: Mapping[Capability | str, CapabilityState | str],
) -> dict[Capability, CapabilityState]:
    if not isinstance(evidence, Mapping):
        raise ReleaseProfileContractError("release_evidence_invalid")
    if len(evidence) > _MAX_EVIDENCE_ENTRIES:
        raise ReleaseProfileContractError("release_evidence_too_large")

    normalized: dict[Capability, CapabilityState] = {}
    for raw_capability, raw_state in evidence.items():
        try:
            capability = (
                raw_capability
                if isinstance(raw_capability, Capability)
                else Capability(str(raw_capability))
            )
        except (TypeError, ValueError) as exc:
            raise ReleaseProfileContractError("release_capability_unknown") from exc
        if capability in normalized:
            raise ReleaseProfileContractError("release_capability_duplicate")
        try:
            state = (
                raw_state
                if isinstance(raw_state, CapabilityState)
                else CapabilityState(str(raw_state))
            )
        except (TypeError, ValueError) as exc:
            raise ReleaseProfileContractError("release_capability_state_invalid") from exc
        normalized[capability] = state
    return normalized


def evaluate_release_profile(
    profile_name: ProfileName | str,
    evidence: Mapping[Capability | str, CapabilityState | str],
) -> ReleaseProfileEvaluation:
    profile = get_profile(profile_name)
    normalized = _normalize_evidence(evidence)
    reason_codes: set[str] = set()
    has_not_ready = False
    has_degraded = False

    for capability, requirement in profile.capabilities.items():
        state = normalized.get(capability, CapabilityState.MISSING)
        prefix = capability.value

        if requirement is Requirement.REQUIRED:
            if state is CapabilityState.MISSING:
                has_not_ready = True
                reason_codes.add(f"{prefix}_required_missing")
            elif state is CapabilityState.DISABLED:
                has_not_ready = True
                reason_codes.add(f"{prefix}_required_disabled")
            elif state is CapabilityState.FAILED:
                has_not_ready = True
                reason_codes.add(f"{prefix}_required_failed")
            elif state is CapabilityState.DEGRADED:
                has_degraded = True
                reason_codes.add(f"{prefix}_required_degraded")
        elif requirement is Requirement.OPTIONAL:
            if state is CapabilityState.DEGRADED:
                has_degraded = True
                reason_codes.add(f"{prefix}_optional_degraded")
            elif state is CapabilityState.FAILED:
                has_degraded = True
                reason_codes.add(f"{prefix}_optional_failed")
        elif state not in {CapabilityState.MISSING, CapabilityState.DISABLED}:
            has_not_ready = True
            reason_codes.add(f"{prefix}_forbidden_enabled")

    status = (
        ReadinessStatus.NOT_READY
        if has_not_ready
        else ReadinessStatus.DEGRADED
        if has_degraded
        else ReadinessStatus.READY
    )
    return ReleaseProfileEvaluation(
        schema_version=SCHEMA_VERSION,
        profile=profile.name.value,
        status=status,
        reason_codes=tuple(sorted(reason_codes)),
    )


def _key_segments(key: str) -> tuple[str, ...]:
    expanded = _ACRONYM_BOUNDARY_RE.sub("_", key)
    expanded = _CAMEL_BOUNDARY_RE.sub("_", expanded).lower()
    return tuple(segment for segment in _KEY_SEGMENT_RE.split(expanded) if segment)


def _sensitive_key(key: str) -> bool:
    segments = _key_segments(key)
    if not segments:
        return False
    if segments[-1] in _SENSITIVE_KEY_TERMINALS:
        return True
    if segments[-1] == "tokens":
        return len(segments) == 1 or segments[-2] not in _NONSECRET_TOKEN_COUNT_PREFIXES
    return len(segments) >= 2 and (segments[-2], segments[-1]) in _SENSITIVE_KEY_PAIRS


def _validate_configuration_shape(value: Any, *, depth: int) -> None:
    if depth > _MAX_CONFIG_DEPTH:
        raise ReleaseProfileContractError("release_configuration_depth_exceeded")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        if abs(value) > 10**18:
            raise ReleaseProfileContractError("release_configuration_integer_invalid")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ReleaseProfileContractError("release_configuration_number_invalid")
        return
    if isinstance(value, str):
        if len(value) > _MAX_CONFIG_STRING_LENGTH:
            raise ReleaseProfileContractError("release_configuration_string_too_long")
        return
    if isinstance(value, Mapping):
        if len(value) > _MAX_CONFIG_ENTRIES:
            raise ReleaseProfileContractError("release_configuration_mapping_too_large")
        seen_keys: set[str] = set()
        for raw_key, child in value.items():
            if not isinstance(raw_key, str) or not raw_key or len(raw_key) > _MAX_CONFIG_KEY_LENGTH:
                raise ReleaseProfileContractError("release_configuration_key_invalid")
            if raw_key in seen_keys:
                raise ReleaseProfileContractError("release_configuration_key_duplicate")
            seen_keys.add(raw_key)
            _validate_configuration_shape(child, depth=depth + 1)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) > _MAX_CONFIG_LIST_ITEMS:
            raise ReleaseProfileContractError("release_configuration_list_too_large")
        for item in value:
            _validate_configuration_shape(item, depth=depth + 1)
        return
    raise ReleaseProfileContractError("release_configuration_type_unsupported")


def _normalize_configuration(value: Any, *, depth: int, sensitive: bool = False) -> Any:
    if sensitive:
        return _REDACTED
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        normalized = {
            raw_key: _normalize_configuration(
                child,
                depth=depth + 1,
                sensitive=_sensitive_key(raw_key),
            )
            for raw_key, child in value.items()
        }
        return {key: normalized[key] for key in sorted(normalized)}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalize_configuration(item, depth=depth + 1) for item in value]
    raise ReleaseProfileContractError("release_configuration_type_unsupported")


def safe_configuration_fingerprint(configuration: Mapping[str, Any]) -> str:
    if not isinstance(configuration, Mapping):
        raise ReleaseProfileContractError("release_configuration_root_invalid")
    _validate_configuration_shape(configuration, depth=0)
    normalized = _normalize_configuration(configuration, depth=0)
    encoded = json.dumps(
        normalized,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
