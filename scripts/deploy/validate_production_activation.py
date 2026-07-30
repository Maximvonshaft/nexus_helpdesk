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
_DIGEST_IMAGE = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
_META_ID = re.compile(r"^[0-9]{5,32}$")
_GRAPH_VERSION = re.compile(r"^v[0-9]{1,2}\.[0-9]{1,2}$")
_EVIDENCE_LABELS = {
    "production_e2e_evidence_url": "production",
    "provider_canary_e2e_evidence_url": "provider_canary",
    "webchat_ai_production_e2e_evidence_url": "webchat_ai",
    "telephony_production_e2e_evidence_url": "telephony",
    "outbound_production_e2e_evidence_url": "outbound",
    "whatsapp_production_e2e_evidence_url": "whatsapp",
    "operations_production_e2e_evidence_url": "operations",
}


class ActivationError(ValueError):
    pass


def _parse_env(paths: list[Path]) -> dict[str, str]:
    values: dict[str, str] = {}
    for path in paths:
        if not path.is_file() or path.is_symlink():
            raise ActivationError(f"env_file_invalid:{path.name}")
        for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
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


def _profiles(values: dict[str, str]) -> set[str]:
    result: set[str] = set()
    for item in values.get("COMPOSE_PROFILES", "").split(","):
        normalized = item.strip().lower()
        if not normalized:
            continue
        if not _TOKEN.fullmatch(normalized):
            raise ActivationError("compose_profile_invalid")
        result.add(normalized)
    return result


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


def _require_https(values: dict[str, str], key: str) -> str:
    value = _require_value(values, key)
    if not value.startswith("https://") or any(char in value for char in "\r\n\x00"):
        raise ActivationError(f"configuration_https_required:{key}")
    return value


def _configured_https(values: dict[str, str], key: str) -> bool:
    value = values.get(key, "").strip()
    if _placeholder(value):
        return False
    _require_https(values, key)
    return True


def _require_digest_image(values: dict[str, str], key: str) -> str:
    value = _require_value(values, key).lower()
    if not _DIGEST_IMAGE.fullmatch(value):
        raise ActivationError(f"configuration_digest_image_required:{key}")
    return value


def _require_meta_id(values: dict[str, str], key: str) -> str:
    value = _require_value(values, key)
    if not _META_ID.fullmatch(value):
        raise ActivationError(f"configuration_meta_id_invalid:{key}")
    return value


def _require_graph_version(values: dict[str, str], key: str) -> str:
    value = _require_value(values, key)
    if not _GRAPH_VERSION.fullmatch(value):
        raise ActivationError(f"configuration_graph_version_invalid:{key}")
    return value


def _require_one_of(values: dict[str, str], *keys: str) -> str:
    for key in keys:
        if not _placeholder(values.get(key, "")):
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
    provider_mode = _token(values, "PROVIDER_RUNTIME_TRAFFIC_MODE", "control")
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
    outbound_email_pilot_enabled = _bool(
        values,
        "OUTBOUND_EMAIL_PRODUCTION_PILOT_ENABLED",
    )
    email_mailbox_sync_enabled = _bool(values, "EMAIL_MAILBOX_SYNC_ENABLED")
    whatsapp_enabled = _bool(values, "WHATSAPP_ENABLED")
    whatsapp_media_enabled = _bool(values, "WHATSAPP_MEDIA_ENABLED")
    whatsapp_media_scanner = _token(
        values,
        "WHATSAPP_MEDIA_SCANNER",
        "disabled",
    )
    embedded_signup_enabled = _bool(values, "WHATSAPP_EMBEDDED_SIGNUP_ENABLED")
    compose_profiles = _profiles(values)
    meta_enabled = _configured_https(values, "WHATSAPP_META_WEBHOOK_PUBLIC_URL")
    baileys_enabled = "whatsapp-baileys" in compose_profiles
    operations_mode = _token(values, "OPERATIONS_DISPATCH_MODE", "disabled")
    operations_adapter = _token(
        values,
        "OPERATIONS_DISPATCH_ADAPTER",
        "disabled",
    )

    if operations_mode != "disabled" or operations_adapter != "disabled":
        raise ActivationError("operations_dispatch_not_implemented")

    if email_mailbox_sync_enabled:
        raise ActivationError("email_mailbox_sync_not_qualified")

    if profile == "provider_canary":
        if not provider_enabled or provider_mode != "canary" or kill_switch:
            raise ActivationError("provider_canary_controls_invalid")
        if not 1 <= percent <= 25:
            raise ActivationError("provider_canary_percent_invalid")
        if webchat_ai_enabled:
            raise ActivationError("provider_canary_webchat_ai_forbidden")
        if (
            voice_enabled
            or outbound_enabled
            or outbound_email_pilot_enabled
            or whatsapp_enabled
            or whatsapp_media_enabled
            or embedded_signup_enabled
        ):
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
        if outbound_email_pilot_enabled and (
            not outbound_enabled or outbound_provider not in {"email", "smtp"}
        ):
            raise ActivationError("outbound_email_pilot_authority_invalid")
        if (
            outbound_enabled
            and outbound_provider in {"email", "smtp"}
            and not outbound_email_pilot_enabled
        ):
            raise ActivationError("outbound_email_pilot_required")

        if whatsapp_enabled:
            if not outbound_enabled or outbound_provider != "native":
                raise ActivationError("whatsapp_outbound_authority_invalid")
            if not meta_enabled and not baileys_enabled:
                raise ActivationError("whatsapp_transport_missing")
            if baileys_enabled:
                _require_digest_image(values, "WHATSAPP_SIDECAR_IMAGE")
        elif baileys_enabled:
            raise ActivationError("whatsapp_baileys_profile_without_whatsapp")

        if embedded_signup_enabled:
            if not whatsapp_enabled or not meta_enabled:
                raise ActivationError("embedded_signup_requires_meta_transport")
            _require_meta_id(values, "WHATSAPP_META_APP_ID")
            _require_value(values, "WHATSAPP_META_APP_SECRET_FILE")
            _require_meta_id(values, "WHATSAPP_META_CONFIGURATION_ID")
            _require_graph_version(values, "WHATSAPP_META_GRAPH_API_VERSION")
            _require_https(values, "WHATSAPP_EMBEDDED_SIGNUP_ALLOWED_ORIGIN")

        if whatsapp_media_enabled and not whatsapp_enabled:
            raise ActivationError("whatsapp_media_requires_whatsapp")
        if whatsapp_media_enabled:
            if whatsapp_media_scanner != "clamav":
                raise ActivationError("whatsapp_media_scanner_not_clamav")
            if _require_value(values, "WHATSAPP_CLAMAV_HOST") != "clamav-controlled":
                raise ActivationError("whatsapp_clamav_host_not_controlled")
            if _int(
                values,
                "WHATSAPP_CLAMAV_PORT",
                3310,
                minimum=1,
                maximum=65535,
            ) != 3310:
                raise ActivationError("whatsapp_clamav_port_not_controlled")
            _int(
                values,
                "WHATSAPP_CLAMAV_TIMEOUT_SECONDS",
                20,
                minimum=1,
                maximum=120,
            )
            _int(
                values,
                "WHATSAPP_MEDIA_MAX_TOTAL_BYTES",
                100 * 1024 * 1024,
                minimum=1024,
                maximum=100 * 1024 * 1024,
            )
            if "whatsapp-media" not in compose_profiles:
                raise ActivationError("whatsapp_media_profile_missing")
            _require_digest_image(values, "WHATSAPP_CLAMAV_IMAGE")
        elif whatsapp_media_scanner != "disabled":
            raise ActivationError("whatsapp_media_scanner_without_media")

    configuration: dict[str, object] = {
        "webchat_ai_enabled": webchat_ai_enabled,
        "voice_enabled": voice_enabled,
        "outbound": {
            "enabled": outbound_enabled,
            "provider": outbound_provider,
            "email_pilot_enabled": outbound_email_pilot_enabled,
        },
        "whatsapp": {
            "enabled": whatsapp_enabled,
            "meta_enabled": meta_enabled,
            "baileys_enabled": baileys_enabled,
            "embedded_signup_enabled": embedded_signup_enabled,
            "media_enabled": whatsapp_media_enabled,
            "media_scanner": whatsapp_media_scanner,
        },
        "email_mailbox_sync_enabled": False,
        "operations_mode": "disabled",
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
        "schema": "nexus.production-activation-preflight.v5",
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
            "outbound_email_pilot": outbound_email_pilot_enabled,
            "email_mailbox_sync": False,
            "whatsapp": whatsapp_enabled,
            "whatsapp_meta": meta_enabled,
            "whatsapp_baileys": baileys_enabled,
            "whatsapp_embedded_signup": embedded_signup_enabled,
            "whatsapp_media": whatsapp_media_enabled,
            "operations": False,
        },
        "whatsapp": {
            "transports": {
                "meta_cloud_api": meta_enabled,
                "baileys_sidecar": baileys_enabled,
            },
            "embedded_signup": embedded_signup_enabled,
            "media_scanner": whatsapp_media_scanner,
            "compose_profiles": sorted(compose_profiles),
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
