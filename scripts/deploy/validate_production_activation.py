#!/usr/bin/env python3
"""Validate production activation overrides before customer traffic is enabled."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

_PROFILES = {"provider_canary", "full"}
_TOKEN = re.compile(r"^[a-z0-9_.-]{1,80}$")
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_DIGEST_IMAGE = re.compile(r"^.+@(sha256:[0-9a-f]{64})$")


class ActivationError(ValueError):
    pass


def _parse_env(paths: list[Path]) -> dict[str, str]:
    values: dict[str, str] = {}
    for path in paths:
        if not path.is_file() or path.is_symlink():
            raise ActivationError(f"env_file_invalid:{path.name}")
        for number, raw in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in raw:
                raise ActivationError(f"env_line_invalid:{path.name}:{number}")
            key, value = raw.split("=", 1)
            key = key.strip()
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", key):
                raise ActivationError(f"env_key_invalid:{path.name}:{number}")
            values[key] = value.strip()
    return values


def _bool(values: dict[str, str], key: str, default: bool = False) -> bool:
    raw = values.get(key)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ActivationError(f"boolean_invalid:{key}")


def _int(
    values: dict[str, str],
    key: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = values.get(key, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError as exc:
        raise ActivationError(f"integer_invalid:{key}") from exc
    if not minimum <= value <= maximum:
        raise ActivationError(f"integer_out_of_range:{key}")
    return value


def _token(values: dict[str, str], key: str, default: str) -> str:
    value = values.get(key, default).strip().lower() or default
    if not _TOKEN.fullmatch(value):
        raise ActivationError(f"token_invalid:{key}")
    return value


def _placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    return (
        not normalized
        or "<" in normalized
        or ">" in normalized
        or "placeholder" in normalized
        or "replace-with" in normalized
        or normalized in {
            "changeme",
            "change-me",
            "replace-me",
            "example-secret",
        }
    )


def _require_https(values: dict[str, str], key: str) -> str:
    value = values.get(key, "").strip()
    if _placeholder(value) or any(char in value for char in "\r\n\x00"):
        raise ActivationError(f"evidence_missing:{key}")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ActivationError(f"evidence_url_invalid:{key}")
    return value


def _require_value(values: dict[str, str], key: str) -> str:
    value = values.get(key, "").strip()
    if _placeholder(value):
        raise ActivationError(f"configuration_missing:{key}")
    return value


def _require_one_of(values: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = values.get(key, "").strip()
        if not _placeholder(value):
            return key
    raise ActivationError(f"configuration_missing_one_of:{','.join(keys)}")


def _evidence_binding(values: dict[str, str]) -> dict[str, str]:
    source_sha = _require_value(values, "GIT_SHA").lower()
    evidence_source_sha = _require_value(
        values,
        "ACTIVATION_EVIDENCE_SOURCE_SHA",
    ).lower()
    if not _SHA40.fullmatch(source_sha):
        raise ActivationError("source_sha_invalid:GIT_SHA")
    if not _SHA40.fullmatch(evidence_source_sha):
        raise ActivationError("source_sha_invalid:ACTIVATION_EVIDENCE_SOURCE_SHA")
    if evidence_source_sha != source_sha:
        raise ActivationError("activation_evidence_source_sha_mismatch")

    image = _require_value(values, "CONTROLLED_IMAGE").lower()
    match = _DIGEST_IMAGE.fullmatch(image)
    if match is None:
        raise ActivationError("controlled_image_not_digest_pinned")
    image_digest = match.group(1)
    evidence_image_digest = _require_value(
        values,
        "ACTIVATION_EVIDENCE_IMAGE_DIGEST",
    ).lower()
    if not _SHA256.fullmatch(evidence_image_digest):
        raise ActivationError("activation_evidence_image_digest_invalid")
    if evidence_image_digest != image_digest:
        raise ActivationError("activation_evidence_image_digest_mismatch")
    return {
        "source_sha": source_sha,
        "image_digest": image_digest,
    }


def validate(values: dict[str, str]) -> dict[str, object]:
    profile = _token(values, "PRODUCTION_PROFILE", "full")
    if profile not in _PROFILES:
        raise ActivationError("production_profile_invalid")

    binding = _evidence_binding(values)
    provider_enabled = _bool(values, "PROVIDER_RUNTIME_ENABLED")
    provider_mode = _token(
        values,
        "PROVIDER_RUNTIME_TRAFFIC_MODE",
        "control",
    )
    kill_switch = _bool(values, "PROVIDER_RUNTIME_KILL_SWITCH", True)
    percent = _int(
        values,
        "PROVIDER_RUNTIME_CANARY_PERCENT",
        0,
        minimum=0,
        maximum=100,
    )
    webchat_ai_enabled = _bool(values, "WEBCHAT_AI_ENABLED")
    human_voice_enabled = _bool(values, "WEBCHAT_HUMAN_CALL_ENABLED")
    live_ai_voice_enabled = _bool(values, "WEBCHAT_LIVE_AI_VOICE_ENABLED")
    voice_enabled = human_voice_enabled or live_ai_voice_enabled
    outbound_enabled = _bool(values, "ENABLE_OUTBOUND_DISPATCH")
    outbound_provider = _token(values, "OUTBOUND_PROVIDER", "disabled")
    operations_mode = _token(
        values,
        "OPERATIONS_DISPATCH_MODE",
        "disabled",
    )
    operations_adapter = _token(
        values,
        "OPERATIONS_DISPATCH_ADAPTER",
        "disabled",
    )

    evidence: dict[str, str] = {}
    if profile == "provider_canary":
        if not provider_enabled or provider_mode != "canary" or kill_switch:
            raise ActivationError("provider_canary_controls_invalid")
        if not 1 <= percent <= 25:
            raise ActivationError("provider_canary_percent_invalid")
        if voice_enabled or outbound_enabled or operations_mode != "disabled":
            raise ActivationError("provider_canary_external_capability_forbidden")
        evidence["provider_canary"] = _require_https(
            values,
            "PROVIDER_CANARY_E2E_EVIDENCE_URL",
        )
    else:
        if (
            not provider_enabled
            or provider_mode != "full"
            or kill_switch
            or percent != 100
        ):
            raise ActivationError("full_provider_controls_invalid")
        evidence["production"] = _require_https(
            values,
            "PRODUCTION_E2E_EVIDENCE_URL",
        )
        if webchat_ai_enabled:
            if _token(values, "WEBCHAT_AI_AUTO_REPLY_MODE", "off") != "runtime":
                raise ActivationError("webchat_ai_runtime_mode_invalid")
            evidence["webchat_ai"] = _require_https(
                values,
                "WEBCHAT_AI_PRODUCTION_E2E_EVIDENCE_URL",
            )
        if voice_enabled:
            if _token(values, "WEBCHAT_VOICE_PROVIDER", "mock") != "livekit":
                raise ActivationError("voice_provider_not_livekit")
            livekit_url = _require_value(values, "LIVEKIT_URL")
            if not livekit_url.startswith("wss://"):
                raise ActivationError("livekit_url_not_wss")
            if not _bool(values, "LIVEKIT_WEBHOOK_ENABLED"):
                raise ActivationError("livekit_webhook_disabled")
            _require_value(values, "LIVEKIT_AGENT_NAME")
            _require_one_of(values, "LIVEKIT_API_KEY", "LIVEKIT_API_KEY_FILE")
            _require_one_of(
                values,
                "LIVEKIT_API_SECRET",
                "LIVEKIT_API_SECRET_FILE",
            )
            _require_one_of(
                values,
                "LIVEKIT_AGENT_SHARED_SECRET",
                "LIVEKIT_AGENT_SHARED_SECRET_FILE",
            )
            if live_ai_voice_enabled:
                _require_value(values, "NEXUS_VOICE_STT_MODEL")
                _require_value(values, "NEXUS_VOICE_TTS_MODEL")
            evidence["telephony"] = _require_https(
                values,
                "TELEPHONY_PRODUCTION_E2E_EVIDENCE_URL",
            )
        if outbound_enabled:
            if outbound_provider == "disabled":
                raise ActivationError("outbound_provider_disabled")
            evidence["outbound"] = _require_https(
                values,
                "OUTBOUND_PRODUCTION_E2E_EVIDENCE_URL",
            )
        if operations_mode != "disabled":
            if operations_adapter == "disabled":
                raise ActivationError("operations_adapter_disabled")
            evidence["operations"] = _require_https(
                values,
                "OPERATIONS_PRODUCTION_E2E_EVIDENCE_URL",
            )

    return {
        "schema": "nexus.production-activation-preflight.v2",
        "status": "pass",
        "profile": profile,
        "candidate": binding,
        "provider": {
            "enabled": provider_enabled,
            "mode": provider_mode,
            "kill_switch": kill_switch,
            "percent": percent,
        },
        "capabilities": {
            "webchat_ai": webchat_ai_enabled,
            "voice": voice_enabled,
            "outbound": outbound_enabled,
            "operations": operations_mode != "disabled",
        },
        "evidence": evidence,
        "contains_secrets": False,
        "external_effects_performed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        payload = validate(_parse_env(args.env_file))
    except (ActivationError, OSError, UnicodeError) as exc:
        print(f"production_activation_preflight_error:{exc}", file=sys.stderr)
        return 2
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
