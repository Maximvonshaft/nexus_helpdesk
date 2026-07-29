from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


_SCHEMA = "nexus.activation-runtime-configuration.v1"
_PROFILE_VALUES = {"controlled", "provider_canary", "full"}
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}

_BOOL_DEFAULTS: dict[str, bool] = {
    "PROVIDER_RUNTIME_ENABLED": False,
    "PROVIDER_RUNTIME_KILL_SWITCH": True,
    "PRIVATE_AI_RUNTIME_ENABLED": False,
    "WEBCHAT_AI_ENABLED": False,
    "WEBCHAT_AI_RECONCILER_ENABLED": False,
    "WEBCHAT_HUMAN_CALL_ENABLED": False,
    "WEBCHAT_LIVE_AI_VOICE_ENABLED": False,
    "LIVEKIT_WEBHOOK_ENABLED": False,
    "ENABLE_OUTBOUND_DISPATCH": False,
    "OUTBOUND_EMAIL_PRODUCTION_PILOT_ENABLED": False,
    "EMAIL_MAILBOX_SYNC_ENABLED": False,
    "WHATSAPP_ENABLED": False,
    "WHATSAPP_EMBEDDED_SIGNUP_ENABLED": False,
    "WHATSAPP_MEDIA_ENABLED": False,
}

_INT_DEFAULTS: dict[str, int] = {
    "PROVIDER_RUNTIME_CANARY_PERCENT": 0,
    "WHATSAPP_CLAMAV_PORT": 3310,
    "WHATSAPP_CLAMAV_TIMEOUT_SECONDS": 20,
    "WHATSAPP_MEDIA_MAX_TOTAL_BYTES": 100 * 1024 * 1024,
}

_TOKEN_DEFAULTS: dict[str, str] = {
    "PROVIDER_RUNTIME_TRAFFIC_MODE": "control",
    "WEBCHAT_AI_AUTO_REPLY_MODE": "off",
    "WEBCHAT_VOICE_PROVIDER": "mock",
    "OUTBOUND_PROVIDER": "disabled",
    "WHATSAPP_MEDIA_SCANNER": "disabled",
    "OPERATIONS_DISPATCH_MODE": "disabled",
    "OPERATIONS_DISPATCH_ADAPTER": "disabled",
}

_TEXT_KEYS = (
    "LIVEKIT_URL",
    "LIVEKIT_AGENT_NAME",
    "NEXUS_VOICE_STT_MODEL",
    "NEXUS_VOICE_TTS_MODEL",
    "WHATSAPP_META_WEBHOOK_PUBLIC_URL",
    "WHATSAPP_SIDECAR_IMAGE",
    "WHATSAPP_META_APP_ID",
    "WHATSAPP_META_CONFIGURATION_ID",
    "WHATSAPP_META_GRAPH_API_VERSION",
    "WHATSAPP_EMBEDDED_SIGNUP_ALLOWED_ORIGIN",
    "WHATSAPP_CLAMAV_IMAGE",
    "WHATSAPP_CLAMAV_HOST",
)

_SECRET_PRESENCE_GROUPS: dict[str, tuple[str, ...]] = {
    "livekit_api_key_configured": ("LIVEKIT_API_KEY", "LIVEKIT_API_KEY_FILE"),
    "livekit_api_secret_configured": (
        "LIVEKIT_API_SECRET",
        "LIVEKIT_API_SECRET_FILE",
    ),
    "livekit_agent_shared_secret_configured": (
        "LIVEKIT_AGENT_SHARED_SECRET",
        "LIVEKIT_AGENT_SHARED_SECRET_FILE",
    ),
    "whatsapp_meta_app_secret_configured": (
        "WHATSAPP_META_APP_SECRET",
        "WHATSAPP_META_APP_SECRET_FILE",
    ),
}


def _bool_value(environment: Mapping[str, str], key: str, default: bool) -> bool:
    raw = environment.get(key)
    if raw is None or not str(raw).strip():
        return default
    normalized = str(raw).strip().lower()
    if normalized in _TRUE:
        return True
    if normalized in _FALSE:
        return False
    raise ValueError(f"activation_runtime_boolean_invalid:{key}")


def _int_value(environment: Mapping[str, str], key: str, default: int) -> int:
    raw = environment.get(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except ValueError as exc:
        raise ValueError(f"activation_runtime_integer_invalid:{key}") from exc


def _token_value(environment: Mapping[str, str], key: str, default: str) -> str:
    return str(environment.get(key) or default).strip().lower() or default


def _text_value(environment: Mapping[str, str], key: str) -> str:
    return str(environment.get(key) or "").strip()


def _profiles(environment: Mapping[str, str]) -> list[str]:
    values = {
        item.strip().lower()
        for item in str(environment.get("COMPOSE_PROFILES") or "").split(",")
        if item.strip()
    }
    return sorted(values)


def _configured(environment: Mapping[str, str], keys: tuple[str, ...]) -> bool:
    return any(bool(str(environment.get(key) or "").strip()) for key in keys)


def canonical_activation_runtime_configuration(
    *,
    profile: str,
    environment: Mapping[str, str],
) -> dict[str, Any]:
    normalized_profile = str(profile or "").strip().lower()
    if normalized_profile not in _PROFILE_VALUES:
        raise ValueError("release_profile_invalid")

    boolean_values = {
        key.lower(): _bool_value(environment, key, default)
        for key, default in _BOOL_DEFAULTS.items()
    }
    integer_values = {
        key.lower(): _int_value(environment, key, default)
        for key, default in _INT_DEFAULTS.items()
    }
    token_values = {
        key.lower(): _token_value(environment, key, default)
        for key, default in _TOKEN_DEFAULTS.items()
    }
    text_values = {key.lower(): _text_value(environment, key) for key in _TEXT_KEYS}
    secret_presence = {
        key: _configured(environment, names)
        for key, names in _SECRET_PRESENCE_GROUPS.items()
    }

    return {
        "schema": _SCHEMA,
        "profile": normalized_profile,
        "booleans": boolean_values,
        "integers": integer_values,
        "tokens": token_values,
        "text": text_values,
        "compose_profiles": _profiles(environment),
        "secret_presence": secret_presence,
        "contains_secrets": False,
    }


def activation_runtime_configuration_digest(
    *,
    profile: str,
    environment: Mapping[str, str],
) -> str:
    configuration = canonical_activation_runtime_configuration(
        profile=profile,
        environment=environment,
    )
    encoded = json.dumps(
        configuration,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
