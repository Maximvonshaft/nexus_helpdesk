#!/usr/bin/env python3
"""Compile operator-assisted WhatsApp live observations into bounded signed evidence.

This command never logs in to Meta, scans a QR code, or fabricates Provider results.
It validates observations collected from the real accepted candidate and emits the
artifact referenced by WHATSAPP_PRODUCTION_E2E_EVIDENCE_URL.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCHEMA = "nexus.whatsapp-live-observation.v1"
_OUTPUT_SCHEMA = "nexus.whatsapp-e2e-evidence.v1"
_TRANSPORTS = ("meta_cloud_api", "baileys_sidecar")
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,255}$")
_FULL_PHONE = re.compile(r"(?<![A-Za-z0-9])\+?[0-9][0-9 ()-]{7,}[0-9](?![A-Za-z0-9])")
_FORBIDDEN_KEYS = {
    "access_token",
    "app_secret",
    "password",
    "qr",
    "qr_data_url",
    "secret",
    "token",
    "phone_number",
    "webhook_verify_token",
}


class EvidenceError(ValueError):
    pass


def compile_evidence(
    observation: dict[str, Any],
    *,
    expected_source_sha: str,
    expected_image_digest: str,
    signing_key: bytes,
    require_media: bool,
) -> dict[str, Any]:
    if observation.get("schema") != _SCHEMA:
        raise EvidenceError("observation_schema_invalid")
    _reject_forbidden_material(observation)
    source_sha = _sha40(expected_source_sha, "expected_source_sha_invalid")
    image_digest = _digest(expected_image_digest, "expected_image_digest_invalid")
    candidate = observation.get("candidate")
    if not isinstance(candidate, dict):
        raise EvidenceError("candidate_missing")
    observed_source_sha = _sha40(
        candidate.get("source_sha"),
        "observed_source_sha_invalid",
    )
    observed_image_digest = _digest(
        candidate.get("image_digest"),
        "observed_image_digest_invalid",
    )
    if observed_source_sha != source_sha:
        raise EvidenceError("candidate_source_sha_mismatch")
    if observed_image_digest != image_digest:
        raise EvidenceError("candidate_image_digest_mismatch")
    observed_at = _timestamp(observation.get("observed_at"), "observed_at_invalid")
    transports = observation.get("transports")
    if not isinstance(transports, dict) or set(transports) != set(_TRANSPORTS):
        raise EvidenceError("dual_transport_observations_required")
    normalized = {
        transport: _transport_evidence(
            transport,
            transports[transport],
            require_media=require_media,
        )
        for transport in _TRANSPORTS
    }
    unsigned = {
        "schema": _OUTPUT_SCHEMA,
        "status": "pass",
        "candidate": {
            "source_sha": source_sha,
            "image_digest": image_digest,
        },
        "observed_at": observed_at,
        "requirements": {
            "transports": list(_TRANSPORTS),
            "media_required": require_media,
            "delivery_states": ["sent", "delivered", "read"],
            "restart_without_reauthentication": True,
        },
        "transports": normalized,
        "contains_secrets": False,
        "contains_full_phone_numbers": False,
        "external_effects_performed": True,
    }
    canonical = _canonical(unsigned)
    if len(signing_key) < 32:
        raise EvidenceError("signing_key_too_short")
    signature = hmac.new(signing_key, canonical, hashlib.sha256).hexdigest()
    return {
        **unsigned,
        "signature": {
            "algorithm": "hmac-sha256",
            "key_id": hashlib.sha256(signing_key).hexdigest()[:16],
            "value": signature,
        },
    }


def verify_compiled_evidence(
    evidence: dict[str, Any],
    *,
    signing_key: bytes,
) -> bool:
    signature = evidence.get("signature")
    if not isinstance(signature, dict):
        return False
    if signature.get("algorithm") != "hmac-sha256":
        return False
    supplied = str(signature.get("value") or "")
    unsigned = dict(evidence)
    unsigned.pop("signature", None)
    expected = hmac.new(signing_key, _canonical(unsigned), hashlib.sha256).hexdigest()
    return len(supplied) == len(expected) and hmac.compare_digest(supplied, expected)


def _transport_evidence(
    transport: str,
    value: Any,
    *,
    require_media: bool,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceError(f"{transport}_observation_invalid")
    if value.get("transport") != transport:
        raise EvidenceError(f"{transport}_identity_mismatch")
    connection_id = _positive_int(
        value.get("connection_id"),
        f"{transport}_connection_id_invalid",
    )
    account_id = _safe_id(value.get("account_id"), f"{transport}_account_id_invalid")
    phone_suffix = str(value.get("phone_suffix") or "")
    if not re.fullmatch(r"[0-9]{4}", phone_suffix):
        raise EvidenceError(f"{transport}_phone_suffix_invalid")
    binding = _mapping(value.get("binding"), f"{transport}_binding_missing")
    if (
        binding.get("observed_state") != "connected"
        or binding.get("authentication_state") != "linked"
        or binding.get("listener_state") != "active"
    ):
        raise EvidenceError(f"{transport}_binding_not_active")
    desired_generation = _nonnegative_int(
        binding.get("desired_generation"),
        f"{transport}_desired_generation_invalid",
    )
    observed_generation = _nonnegative_int(
        binding.get("observed_generation"),
        f"{transport}_observed_generation_invalid",
    )
    if desired_generation != observed_generation:
        raise EvidenceError(f"{transport}_generation_mismatch")

    inbound = _message_direction(
        transport,
        "inbound",
        value.get("inbound"),
    )
    if inbound.get("stored") is not True or inbound.get("idempotent_replay") is not True:
        raise EvidenceError(f"{transport}_inbound_durability_unproven")
    outbound = _message_direction(
        transport,
        "outbound",
        value.get("outbound"),
    )
    restart = _mapping(value.get("restart"), f"{transport}_restart_missing")
    initiated_at = _timestamp(
        restart.get("initiated_at"),
        f"{transport}_restart_initiated_at_invalid",
    )
    reconnected_at = _timestamp(
        restart.get("reconnected_at"),
        f"{transport}_restart_reconnected_at_invalid",
    )
    if _parse_timestamp(reconnected_at) < _parse_timestamp(initiated_at):
        raise EvidenceError(f"{transport}_restart_order_invalid")
    for field in (
        "credentials_persisted",
        "listener_active",
        "reconnected_without_reauthentication",
    ):
        if restart.get(field) is not True:
            raise EvidenceError(f"{transport}_restart_{field}_unproven")
    if _nonnegative_int(
        restart.get("desired_generation"),
        f"{transport}_restart_desired_generation_invalid",
    ) != _nonnegative_int(
        restart.get("observed_generation"),
        f"{transport}_restart_observed_generation_invalid",
    ):
        raise EvidenceError(f"{transport}_restart_generation_mismatch")

    media = value.get("media")
    if require_media:
        media = _media_evidence(transport, media)
    elif media is not None:
        media = _media_evidence(transport, media)
    return {
        "transport": transport,
        "connection_id": connection_id,
        "account_id": account_id,
        "phone_suffix": phone_suffix,
        "binding": {
            "observed_state": "connected",
            "authentication_state": "linked",
            "listener_state": "active",
            "desired_generation": desired_generation,
            "observed_generation": observed_generation,
        },
        "inbound": inbound,
        "outbound": outbound,
        "restart": {
            "initiated_at": initiated_at,
            "reconnected_at": reconnected_at,
            "credentials_persisted": True,
            "listener_active": True,
            "reconnected_without_reauthentication": True,
            "desired_generation": int(restart["desired_generation"]),
            "observed_generation": int(restart["observed_generation"]),
        },
        "media": media,
    }


def _message_direction(transport: str, direction: str, value: Any) -> dict[str, Any]:
    row = _mapping(value, f"{transport}_{direction}_missing")
    provider_message_id = _safe_id(
        row.get("provider_message_id"),
        f"{transport}_{direction}_provider_message_id_invalid",
    )
    if direction == "inbound":
        received_at = _timestamp(
            row.get("received_at"),
            f"{transport}_inbound_received_at_invalid",
        )
        return {
            "provider_message_id": provider_message_id,
            "received_at": received_at,
            "stored": row.get("stored") is True,
            "idempotent_replay": row.get("idempotent_replay") is True,
        }
    sent_at = _timestamp(row.get("sent_at"), f"{transport}_sent_at_invalid")
    delivered_at = _timestamp(
        row.get("delivered_at"),
        f"{transport}_delivered_at_invalid",
    )
    read_at = _timestamp(row.get("read_at"), f"{transport}_read_at_invalid")
    if not (
        _parse_timestamp(sent_at)
        <= _parse_timestamp(delivered_at)
        <= _parse_timestamp(read_at)
    ):
        raise EvidenceError(f"{transport}_delivery_order_invalid")
    return {
        "provider_message_id": provider_message_id,
        "sent_at": sent_at,
        "delivered_at": delivered_at,
        "read_at": read_at,
    }


def _media_evidence(transport: str, value: Any) -> dict[str, Any]:
    row = _mapping(value, f"{transport}_media_missing")
    inbound = _mapping(row.get("inbound"), f"{transport}_media_inbound_missing")
    outbound = _mapping(row.get("outbound"), f"{transport}_media_outbound_missing")
    if inbound.get("scan_status") != "clean" or inbound.get("storage_status") != "available":
        raise EvidenceError(f"{transport}_media_scan_or_storage_unproven")
    sha256 = str(inbound.get("sha256") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise EvidenceError(f"{transport}_media_sha256_invalid")
    return {
        "inbound": {
            "provider_message_id": _safe_id(
                inbound.get("provider_message_id"),
                f"{transport}_media_inbound_provider_id_invalid",
            ),
            "asset_id": _positive_int(
                inbound.get("asset_id"),
                f"{transport}_media_asset_id_invalid",
            ),
            "attachment_id": _positive_int(
                inbound.get("attachment_id"),
                f"{transport}_media_attachment_id_invalid",
            ),
            "scan_status": "clean",
            "storage_status": "available",
            "sha256": sha256,
            "byte_size": _positive_int(
                inbound.get("byte_size"),
                f"{transport}_media_byte_size_invalid",
            ),
        },
        "outbound": _message_direction(
            transport,
            "outbound",
            outbound,
        ),
    }


def _reject_forbidden_material(value: Any, *, path: str = "root") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in _FORBIDDEN_KEYS or normalized.endswith("_secret") or normalized.endswith("_token"):
                raise EvidenceError(f"forbidden_evidence_field:{path}.{normalized}")
            _reject_forbidden_material(item, path=f"{path}.{normalized}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_forbidden_material(item, path=f"{path}[{index}]")
        return
    if isinstance(value, str) and _FULL_PHONE.search(value):
        raise EvidenceError(f"full_phone_number_forbidden:{path}")


def _mapping(value: Any, code: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceError(code)
    return value


def _positive_int(value: Any, code: str) -> int:
    parsed = _nonnegative_int(value, code)
    if parsed <= 0:
        raise EvidenceError(code)
    return parsed


def _nonnegative_int(value: Any, code: str) -> int:
    if isinstance(value, bool):
        raise EvidenceError(code)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise EvidenceError(code) from exc
    if parsed < 0:
        raise EvidenceError(code)
    return parsed


def _safe_id(value: Any, code: str) -> str:
    normalized = str(value or "").strip()
    if not _SAFE_ID.fullmatch(normalized):
        raise EvidenceError(code)
    return normalized


def _sha40(value: Any, code: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA40.fullmatch(normalized):
        raise EvidenceError(code)
    return normalized


def _digest(value: Any, code: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256.fullmatch(normalized):
        raise EvidenceError(code)
    return normalized.removeprefix("sha256:")


def _timestamp(value: Any, code: str) -> str:
    normalized = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceError(code) from exc
    if parsed.tzinfo is None:
        raise EvidenceError(code)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--expected-source-sha", required=True)
    parser.add_argument("--expected-image-digest", required=True)
    parser.add_argument("--signing-key-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-media", action="store_true")
    args = parser.parse_args()
    try:
        if not args.input.is_file() or args.input.is_symlink():
            raise EvidenceError("observation_file_invalid")
        if not args.signing_key_file.is_file() or args.signing_key_file.is_symlink():
            raise EvidenceError("signing_key_file_invalid")
        observation = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(observation, dict):
            raise EvidenceError("observation_root_invalid")
        evidence = compile_evidence(
            observation,
            expected_source_sha=args.expected_source_sha,
            expected_image_digest=args.expected_image_digest,
            signing_key=args.signing_key_file.read_bytes().strip(),
            require_media=args.require_media,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, EvidenceError) as exc:
        print(f"whatsapp_e2e_evidence_error:{exc}")
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": evidence["status"],
                "source_sha": evidence["candidate"]["source_sha"],
                "media_required": evidence["requirements"]["media_required"],
                "transports": list(evidence["transports"]),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
