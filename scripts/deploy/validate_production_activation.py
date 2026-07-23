#!/usr/bin/env python3
"""Validate production activation overrides before customer traffic is enabled."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.activation_evidence_policy import (  # noqa: E402
    activation_evidence_snapshot,
)

_PROFILES = {"provider_canary", "full"}
_TOKEN = re.compile(r"^[a-z0-9_.-]{1,80}$")
_EVIDENCE_LABELS = {
    "production_e2e_evidence_url": "production",
    "provider_canary_e2e_evidence_url": "provider_canary",
    "webchat_ai_production_e2e_evidence_url": "webchat_ai",
    "telephony_production_e2e_evidence_url": "telephony",
    "outbound_production_e2e_evidence_url": "outbound",
    "operations_production_e2e_evidence_url": "operations",
}


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
    )


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


def _activation_evidence(
    values: dict[str, str],
    *,
    profile: str,
    configuration: dict[str, object],
) -> dict[str, object]:
    snapshot = activation_evidence_snapshot(
        profile=profile,
        configuration=configuration,
        identity={
            "source_sha": values.get("GIT_SHA"),
            "image": values.get("CONTROLLED_IMAGE"),
        },
        environment=values,
    )
    if snapshot["status"] != "ready":
        reasons = snapshot.get("reason_codes") or ["activation_evidence_not_ready"]
        raise ActivationError(str(reasons[0]))
    return snapshot


def validate(values: dict[str, str]) -> dict[str, object]:
    profile = _token(values, "PRODUCTION_PROFILE", "full")
    if profile not in _PROFILES:
        raise ActivationError("production_profile_invalid")

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

    if profile == "provider_canary":
        if not provider_enabled or provider_mode != "canary" or kill_switch:
            raise ActivationError("provider_canary_controls_invalid")
        if not 1 <= percent <= 25:
            raise ActivationError("provider_canary_percent_invalid")
        if webchat_ai_enabled:
            raise ActivationError("provider_canary_webchat_ai_forbidden")
        if voice_enabled or outbound_enabled or operations_mode != "disabled":
            raise ActivationError("provider_canary_external_capability_forbidden")
    else:
        if (
            not provider_enabled
            or provider_mode != "full"
            or kill_switch
            or percent != 100
        ):
            raise ActivationError("full_provider_controls_invalid")
        if webchat_ai_enabled and (
            _token(values, "WEBCHAT_AI_AUTO_REPLY_MODE", "off") != "runtime"
        ):
            raise ActivationError("webchat_ai_runtime_mode_invalid")
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
        if outbound_enabled and outbound_provider == "disabled":
            raise ActivationError("outbound_provider_disabled")
        if operations_mode != "disabled" and operations_adapter == "disabled":
            raise ActivationError("operations_adapter_disabled")

    configuration: dict[str, object] = {
        "webchat_ai_enabled": webchat_ai_enabled,
        "voice_enabled": voice_enabled,
        "outbound": {
            "enabled": outbound_enabled,
            "provider": outbound_provider,
        },
        "operations_mode": operations_mode,
    }
    activation = _activation_evidence(
        values,
        profile=profile,
        configuration=configuration,
    )
    evidence = {
        _EVIDENCE_LABELS.get(key, key): value
        for key, value in dict(activation.get("references") or {}).items()
    }

    return {
        "schema": "nexus.production-activation-preflight.v2",
        "status": "pass",
        "profile": profile,
        "candidate": activation.get("candidate") or {},
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


def _input_values(args: argparse.Namespace) -> dict[str, str]:
    if args.environment:
        if args.env_file:
            raise ActivationError("activation_input_modes_conflict")
        return dict(os.environ)
    if not args.env_file:
        raise ActivationError("activation_input_required")
    return _parse_env(args.env_file)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", action="append", type=Path)
    parser.add_argument("--environment", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        payload = validate(_input_values(args))
    except (ActivationError, OSError, UnicodeError, ValueError) as exc:
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
