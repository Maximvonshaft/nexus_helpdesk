from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class WhatsAppRuntimeSettings:
    enabled: bool
    baileys_sidecar_url: str
    baileys_sidecar_token: str | None
    connector_key: str | None
    connector_hmac_secret: str | None
    transport_timeout_seconds: int
    connector_timestamp_tolerance_seconds: int
    meta_webhook_public_url: str | None


@lru_cache(maxsize=1)
def get_whatsapp_runtime_settings() -> WhatsAppRuntimeSettings:
    app_env = os.getenv("APP_ENV", "development").strip().lower()
    is_production = app_env == "production"
    enabled = _bool("WHATSAPP_ENABLED", False)
    timeout = _bounded_int("WHATSAPP_TRANSPORT_TIMEOUT_SECONDS", 10, 1, 60)
    tolerance = _bounded_int(
        "WHATSAPP_CONNECTOR_TIMESTAMP_TOLERANCE_SECONDS",
        300,
        30,
        3600,
    )
    sidecar_url = (
        os.getenv(
            "WHATSAPP_BAILEYS_SIDECAR_URL",
            "http://127.0.0.1:18793",
        )
        .strip()
        .rstrip("/")
    )
    if enabled and not sidecar_url.startswith(("http://", "https://")):
        raise RuntimeError("WHATSAPP_BAILEYS_SIDECAR_URL must be an http(s) URL")

    # Baileys transport credentials are intentionally optional at the global
    # runtime layer. A Meta-only deployment must be fully valid without a
    # sidecar, and a Baileys operation fails closed at its adapter boundary when
    # its secrets are absent. This keeps both transports independently usable.
    sidecar_token = _secret(
        value_env="WHATSAPP_BAILEYS_SIDECAR_TOKEN",
        file_env="WHATSAPP_BAILEYS_SIDECAR_TOKEN_FILE",
        default_file="/run/nexus/whatsapp_baileys_sidecar_token",
        is_production=is_production,
    )
    connector_key = _secret(
        value_env="WHATSAPP_CONNECTOR_KEY",
        file_env="WHATSAPP_CONNECTOR_KEY_FILE",
        default_file="/run/nexus/whatsapp_connector_key",
        is_production=is_production,
    )
    connector_hmac_secret = _secret(
        value_env="WHATSAPP_CONNECTOR_HMAC_SECRET",
        file_env="WHATSAPP_CONNECTOR_HMAC_SECRET_FILE",
        default_file="/run/nexus/whatsapp_connector_hmac_secret",
        is_production=is_production,
    )
    meta_webhook_public_url = (
        os.getenv("WHATSAPP_META_WEBHOOK_PUBLIC_URL", "").strip() or None
    )
    if meta_webhook_public_url and not meta_webhook_public_url.startswith("https://"):
        raise RuntimeError("WHATSAPP_META_WEBHOOK_PUBLIC_URL must use HTTPS")

    return WhatsAppRuntimeSettings(
        enabled=enabled,
        baileys_sidecar_url=sidecar_url,
        baileys_sidecar_token=sidecar_token,
        connector_key=connector_key,
        connector_hmac_secret=connector_hmac_secret,
        transport_timeout_seconds=timeout,
        connector_timestamp_tolerance_seconds=tolerance,
        meta_webhook_public_url=meta_webhook_public_url,
    )


def reset_whatsapp_runtime_settings_cache() -> None:
    get_whatsapp_runtime_settings.cache_clear()


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be a boolean")


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    try:
        value = int(raw) if raw is not None else default
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(
            f"{name} must be between {minimum} and {maximum}"
        )
    return value


def _secret(
    *,
    value_env: str,
    file_env: str,
    default_file: str,
    is_production: bool,
) -> str | None:
    configured_file = os.getenv(file_env, "").strip()
    candidate = Path(configured_file or default_file)
    if candidate.is_file() and not candidate.is_symlink():
        value = candidate.read_text(encoding="utf-8").strip()
        return value or None
    raw = os.getenv(value_env, "").strip()
    if raw and is_production:
        raise RuntimeError(
            f"production prohibits plain text {value_env}; use {file_env}"
        )
    return raw or None
