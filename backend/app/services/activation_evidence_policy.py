from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_PROFILE_VALUES = {"controlled", "provider_canary", "full"}
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_DIGEST_IMAGE = re.compile(r"^.+@(sha256:[0-9a-f]{64})$")
_ENVIRONMENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{2,159}$")
_MAX_MANIFEST_BYTES = 1024 * 1024
_TEST_UNSIGNED_FLAG = "ACTIVATION_EVIDENCE_TEST_ALLOW_UNSIGNED"


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
    whatsapp = configuration.get("whatsapp") or {}
    if isinstance(whatsapp, Mapping) and whatsapp.get("enabled"):
        required.append("WHATSAPP_PRODUCTION_E2E_EVIDENCE_URL")
    if configuration.get("operations_mode") not in {None, "", "disabled"}:
        required.append("OPERATIONS_PRODUCTION_E2E_EVIDENCE_URL")
    return tuple(dict.fromkeys(required))


def _test_unsigned_allowed(environment: Mapping[str, str]) -> bool:
    return (
        str(environment.get("APP_ENV") or "").strip().lower() == "test"
        and str(environment.get(_TEST_UNSIGNED_FLAG) or "").strip().lower()
        in {"1", "true", "yes", "on"}
    )


def _candidate_binding(
    *,
    profile: str,
    identity: Mapping[str, Any],
    environment: Mapping[str, str],
    require_signed_manifest: bool,
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
    configuration_digest = str(
        environment.get("ACTIVATION_EVIDENCE_CONFIGURATION_DIGEST") or ""
    ).strip().lower()
    environment_id = str(
        environment.get("ACTIVATION_EVIDENCE_ENVIRONMENT_ID") or ""
    ).strip()
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

    candidate: dict[str, str | None] = {
        "source_sha": evidence_source_sha or None,
        "image_digest": evidence_image_digest or None,
        "runtime_source_sha": source_sha or None,
        "runtime_image_digest": image_digest,
    }
    if require_signed_manifest:
        if not _SHA256.fullmatch(configuration_digest):
            reason_codes.append("activation_evidence_configuration_digest_invalid")
        if not _ENVIRONMENT_ID.fullmatch(environment_id):
            reason_codes.append("activation_evidence_environment_id_invalid")
        candidate["configuration_digest"] = configuration_digest or None
        candidate["environment_id"] = environment_id or None

    return candidate, reason_codes


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _bounded_file(path_value: str, *, field: str) -> bytes:
    path = Path(path_value)
    if not path.is_absolute():
        raise ValueError(f"{field}_absolute_path_required")
    try:
        with path.open("rb") as handle:
            payload = handle.read(_MAX_MANIFEST_BYTES + 1)
    except OSError as exc:
        raise ValueError(f"{field}_unavailable") from exc
    if len(payload) > _MAX_MANIFEST_BYTES:
        raise ValueError(f"{field}_too_large")
    return payload


def _manifest_and_key(environment: Mapping[str, str]) -> tuple[dict[str, Any], bytes]:
    app_env = str(environment.get("APP_ENV") or "").strip().lower()
    inline_manifest = str(
        environment.get("ACTIVATION_EVIDENCE_MANIFEST_JSON") or ""
    ).strip()
    inline_key = str(environment.get("ACTIVATION_EVIDENCE_SIGNING_KEY") or "")

    if inline_manifest or inline_key:
        if app_env != "test":
            raise ValueError("activation_evidence_inline_material_forbidden")
        if not inline_manifest or not inline_key:
            raise ValueError("activation_evidence_inline_material_incomplete")
        manifest_bytes = inline_manifest.encode("utf-8")
        key = inline_key.encode("utf-8")
    else:
        manifest_path = str(
            environment.get("ACTIVATION_EVIDENCE_MANIFEST_FILE") or ""
        ).strip()
        key_path = str(
            environment.get("ACTIVATION_EVIDENCE_SIGNING_KEY_FILE") or ""
        ).strip()
        if _placeholder(manifest_path):
            raise ValueError("activation_evidence_manifest_missing")
        if _placeholder(key_path):
            raise ValueError("activation_evidence_signing_key_missing")
        manifest_bytes = _bounded_file(
            manifest_path,
            field="activation_evidence_manifest",
        )
        key = _bounded_file(
            key_path,
            field="activation_evidence_signing_key",
        ).strip()

    if len(key) < 32:
        raise ValueError("activation_evidence_signing_key_too_short")
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("activation_evidence_manifest_invalid") from exc
    if not isinstance(manifest, dict):
        raise ValueError("activation_evidence_manifest_not_object")
    return manifest, key


def _verified_manifest(
    environment: Mapping[str, str],
    *,
    candidate: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    manifest, key = _manifest_and_key(environment)
    if manifest.get("schema") != "nexus.activation-evidence.v2":
        raise ValueError("activation_evidence_manifest_schema_invalid")

    signature = manifest.get("signature")
    if not isinstance(signature, dict):
        raise ValueError("activation_evidence_signature_missing")
    if signature.get("algorithm") != "hmac-sha256":
        raise ValueError("activation_evidence_signature_algorithm_invalid")
    signature_value = str(signature.get("value") or "").strip().lower()
    if not _HEX64.fullmatch(signature_value):
        raise ValueError("activation_evidence_signature_invalid")

    unsigned = dict(manifest)
    unsigned.pop("signature", None)
    expected = hmac.new(key, _canonical_json(unsigned), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature_value):
        raise ValueError("activation_evidence_signature_mismatch")

    manifest_candidate = manifest.get("candidate")
    if not isinstance(manifest_candidate, dict):
        raise ValueError("activation_evidence_manifest_candidate_missing")
    for field in (
        "source_sha",
        "image_digest",
        "configuration_digest",
        "environment_id",
    ):
        if manifest_candidate.get(field) != candidate.get(field):
            raise ValueError(f"activation_evidence_manifest_{field}_mismatch")

    evidence = manifest.get("evidence")
    if not isinstance(evidence, dict):
        raise ValueError("activation_evidence_manifest_evidence_missing")
    manifest_sha256 = "sha256:" + hashlib.sha256(_canonical_json(manifest)).hexdigest()
    return manifest, manifest_sha256


def _valid_timestamp(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    try:
        observed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    if observed.tzinfo is None:
        return False
    return observed.astimezone(timezone.utc) <= datetime.now(timezone.utc).replace(
        microsecond=999999
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
    required = _required_evidence_keys(normalized_profile, configuration)
    require_signed_manifest = bool(required) and not _test_unsigned_allowed(
        active_environment
    )
    candidate, reason_codes = _candidate_binding(
        profile=normalized_profile,
        identity=identity,
        environment=active_environment,
        require_signed_manifest=require_signed_manifest,
    )

    manifest: dict[str, Any] | None = None
    manifest_sha256: str | None = None
    if require_signed_manifest and candidate is not None:
        try:
            manifest, manifest_sha256 = _verified_manifest(
                active_environment,
                candidate=candidate,
            )
        except ValueError as exc:
            reason_codes.append(str(exc))

    manifest_evidence = (
        manifest.get("evidence")
        if isinstance(manifest, dict) and isinstance(manifest.get("evidence"), dict)
        else {}
    )
    references: dict[str, str] = {}
    receipts: dict[str, dict[str, Any]] = {}
    for key in required:
        normalized_key = key.lower()
        value = str(active_environment.get(key) or "").strip()
        if _placeholder(value) or any(char in value for char in "\r\n\x00"):
            reason_codes.append(f"activation_evidence_missing:{normalized_key}")
            continue
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.netloc:
            reason_codes.append(f"activation_evidence_invalid:{normalized_key}")
            continue

        if require_signed_manifest:
            entry = manifest_evidence.get(normalized_key)
            if not isinstance(entry, dict):
                reason_codes.append(
                    f"activation_evidence_manifest_entry_missing:{normalized_key}"
                )
                continue
            if entry.get("url") != value:
                reason_codes.append(
                    f"activation_evidence_manifest_url_mismatch:{normalized_key}"
                )
            if entry.get("result") != "pass":
                reason_codes.append(
                    f"activation_evidence_manifest_result_not_pass:{normalized_key}"
                )
            artifact_sha256 = str(entry.get("artifact_sha256") or "").lower()
            if not _SHA256.fullmatch(artifact_sha256):
                reason_codes.append(
                    f"activation_evidence_manifest_artifact_digest_invalid:{normalized_key}"
                )
            for field in (
                "source_sha",
                "image_digest",
                "configuration_digest",
                "environment_id",
            ):
                if entry.get(field) != candidate.get(field):
                    reason_codes.append(
                        f"activation_evidence_manifest_entry_{field}_mismatch:{normalized_key}"
                    )
            if not _valid_timestamp(entry.get("generated_at")):
                reason_codes.append(
                    f"activation_evidence_manifest_generated_at_invalid:{normalized_key}"
                )
            receipts[normalized_key] = {
                "url": value,
                "artifact_sha256": artifact_sha256 or None,
                "result": entry.get("result"),
                "generated_at": entry.get("generated_at"),
            }
        references[normalized_key] = value

    return {
        "schema": "nexus.activation-evidence.v2",
        "status": "ready" if not reason_codes else "not_ready",
        "required": [key.lower() for key in required],
        "references": references,
        "receipts": receipts,
        "manifest_sha256": manifest_sha256,
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
    whatsapp = dict(configuration.get("whatsapp") or {})

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
            "whatsapp_enablement_authorized": bool(
                production_authorized
                and whatsapp.get("enabled")
                and outbound.get("enabled")
                and outbound.get("provider") != "disabled"
            ),
            "whatsapp_media_enablement_authorized": bool(
                production_authorized
                and whatsapp.get("enabled")
                and whatsapp.get("media_enabled")
                and whatsapp.get("media_scanner") == "clamav"
            ),
            "operations_enablement_authorized": bool(
                production_authorized
                and configuration.get("operations_mode")
                not in {None, "", "disabled"}
            ),
        }
    )
    return payload
