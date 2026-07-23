from __future__ import annotations

import os
import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any
from urllib.parse import urlsplit

_PROFILE_VALUES = {"controlled", "provider_canary", "full"}
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_DIGEST_IMAGE = re.compile(r"^.+@(sha256:[0-9a-f]{64})$")


def _placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    return (
        not normalized
        or "<" in normalized
        or ">" in normalized
        or "placeholder" in normalized
        or "replace-with" in normalized
        or normalized
        in {
            "changeme",
            "change-me",
            "replace-me",
            "example-secret",
        }
    )


def _required_evidence_keys(
    profile: str,
    configuration: Mapping[str, Any],
) -> tuple[str, ...]:
    if profile == "controlled":
        return ()
    if profile == "provider_canary":
        return ("PROVIDER_CANARY_E2E_EVIDENCE_URL",)

    required = ["PRODUCTION_E2E_EVIDENCE_URL"]
    if configuration.get("webchat_ai_enabled"):
        required.append("WEBCHAT_AI_PRODUCTION_E2E_EVIDENCE_URL")
    if configuration.get("voice_enabled"):
        required.append("TELEPHONY_PRODUCTION_E2E_EVIDENCE_URL")
    outbound = configuration.get("outbound") or {}
    if isinstance(outbound, Mapping) and outbound.get("enabled"):
        required.append("OUTBOUND_PRODUCTION_E2E_EVIDENCE_URL")
    if configuration.get("operations_mode") not in {None, "", "disabled"}:
        required.append("OPERATIONS_PRODUCTION_E2E_EVIDENCE_URL")
    return tuple(required)


def _candidate_binding(
    *,
    profile: str,
    identity: Mapping[str, Any],
    environment: Mapping[str, str],
) -> tuple[dict[str, str | None] | None, list[str]]:
    if profile == "controlled":
        return None, []

    source_sha = str(identity.get("source_sha") or "").strip().lower()
    image = str(identity.get("image") or "").strip().lower()
    image_match = _DIGEST_IMAGE.fullmatch(image)
    image_digest = image_match.group(1) if image_match else None
    evidence_source_sha = str(
        environment.get("ACTIVATION_EVIDENCE_SOURCE_SHA") or ""
    ).strip().lower()
    evidence_image_digest = str(
        environment.get("ACTIVATION_EVIDENCE_IMAGE_DIGEST") or ""
    ).strip().lower()
    reason_codes: list[str] = []

    if not _SHA40.fullmatch(source_sha):
        reason_codes.append("activation_candidate_source_sha_invalid")
    if not _SHA40.fullmatch(evidence_source_sha):
        reason_codes.append("activation_evidence_source_sha_invalid")
    elif evidence_source_sha != source_sha:
        reason_codes.append("activation_evidence_source_sha_mismatch")

    if image_digest is None:
        reason_codes.append("activation_candidate_image_digest_invalid")
    if not _SHA256.fullmatch(evidence_image_digest):
        reason_codes.append("activation_evidence_image_digest_invalid")
    elif evidence_image_digest != image_digest:
        reason_codes.append("activation_evidence_image_digest_mismatch")

    return (
        {
            "source_sha": evidence_source_sha or None,
            "image_digest": evidence_image_digest or None,
            "runtime_source_sha": source_sha or None,
            "runtime_image_digest": image_digest,
        },
        reason_codes,
    )


def activation_evidence_snapshot(
    *,
    profile: str,
    configuration: Mapping[str, Any],
    identity: Mapping[str, Any],
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    normalized_profile = profile.strip().lower()
    if normalized_profile not in _PROFILE_VALUES:
        raise ValueError("release_profile_invalid")
    active_environment = environment if environment is not None else os.environ
    candidate, reason_codes = _candidate_binding(
        profile=normalized_profile,
        identity=identity,
        environment=active_environment,
    )
    required = _required_evidence_keys(normalized_profile, configuration)
    references: dict[str, str] = {}
    for key in required:
        value = str(active_environment.get(key) or "").strip()
        if _placeholder(value) or any(char in value for char in "\r\n\x00"):
            reason_codes.append(f"activation_evidence_missing:{key.lower()}")
            continue
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.netloc:
            reason_codes.append(f"activation_evidence_invalid:{key.lower()}")
            continue
        references[key.lower()] = value

    return {
        "schema": "nexus.activation-evidence.v1",
        "status": "ready" if not reason_codes else "not_ready",
        "required": [key.lower() for key in required],
        "references": references,
        "candidate": candidate,
        "reason_codes": sorted(set(reason_codes)),
        "contains_secrets": False,
    }


def finalize_release_readiness(
    collected: Mapping[str, Any],
    *,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    payload = deepcopy(dict(collected))
    profile = str(payload.get("profile") or "controlled").strip().lower()
    collectors = deepcopy(dict(payload.get("collectors") or {}))
    identity = dict(collectors.get("identity") or {})
    configuration = dict(collectors.get("configuration") or {})
    activation = activation_evidence_snapshot(
        profile=profile,
        configuration=configuration,
        identity=identity,
        environment=environment,
    )
    collectors["activation_evidence"] = activation

    reason_codes = {
        str(code)
        for code in payload.get("reason_codes") or []
        if not str(code).startswith("activation:")
    }
    reason_codes.update(
        f"activation:{code}" for code in activation["reason_codes"]
    )
    status = "ready" if not reason_codes else "not_ready"
    production_authorized = status == "ready" and profile == "full"
    provider_authorized = status == "ready" and profile in {
        "provider_canary",
        "full",
    }
    telephony = dict(collectors.get("telephony") or {})
    outbound = dict(configuration.get("outbound") or {})

    payload.update(
        {
            "schema": "nexus.release-readiness.v2",
            "profile": profile,
            "status": status,
            "reason_codes": sorted(reason_codes),
            "collectors": collectors,
            "production_authorized": production_authorized,
            "provider_enablement_authorized": provider_authorized,
            "webchat_ai_enablement_authorized": bool(
                production_authorized
                and configuration.get("webchat_ai_enabled")
            ),
            "voice_enablement_authorized": bool(
                production_authorized
                and telephony.get("enabled")
                and telephony.get("status") == "ready"
            ),
            "outbound_enablement_authorized": bool(
                production_authorized
                and outbound.get("enabled")
                and outbound.get("provider") != "disabled"
            ),
            "operations_enablement_authorized": bool(
                production_authorized
                and configuration.get("operations_mode")
                not in {None, "", "disabled"}
            ),
        }
    )
    return payload
