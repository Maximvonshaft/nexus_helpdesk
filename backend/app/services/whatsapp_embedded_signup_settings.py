from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class WhatsAppEmbeddedSignupSettings:
    enabled: bool
    app_id: str | None
    app_secret: str | None
    configuration_id: str | None
    graph_api_version: str | None
    allowed_origin: str | None
    session_ttl_seconds: int


@lru_cache(maxsize=1)
def get_whatsapp_embedded_signup_settings() -> WhatsAppEmbeddedSignupSettings:
    app_env = os.getenv("APP_ENV", "development").strip().lower()
    enabled = _bool("WHATSAPP_EMBEDDED_SIGNUP_ENABLED", False)
    app_id = _optional("WHATSAPP_META_APP_ID")
    configuration_id = _optional("WHATSAPP_META_CONFIGURATION_ID")
    graph_version = _optional("WHATSAPP_META_GRAPH_API_VERSION")
    allowed_origin = _optional("WHATSAPP_EMBEDDED_SIGNUP_ALLOWED_ORIGIN")
    app_secret = _secret(
        value_env="WHATSAPP_META_APP_SECRET",
        file_env="WHATSAPP_META_APP_SECRET_FILE",
        default_file="/run/nexus/whatsapp_meta_app_secret",
        production=app_env == "production",
    )
    ttl = _int("WHATSAPP_EMBEDDED_SIGNUP_SESSION_TTL_SECONDS", 600, 120, 1800)
    if enabled:
        missing = [
            name
            for name, value in (
                ("WHATSAPP_META_APP_ID", app_id),
                ("WHATSAPP_META_APP_SECRET_FILE", app_secret),
                ("WHATSAPP_META_CONFIGURATION_ID", configuration_id),
                ("WHATSAPP_META_GRAPH_API_VERSION", graph_version),
                ("WHATSAPP_EMBEDDED_SIGNUP_ALLOWED_ORIGIN", allowed_origin),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                "WhatsApp Embedded Signup settings missing: " + ",".join(missing)
            )
        if not str(allowed_origin).startswith("https://"):
            raise RuntimeError(
                "WHATSAPP_EMBEDDED_SIGNUP_ALLOWED_ORIGIN must use HTTPS"
            )
        if not str(graph_version).startswith("v") or "." not in str(graph_version):
            raise RuntimeError("WHATSAPP_META_GRAPH_API_VERSION is invalid")
    return WhatsAppEmbeddedSignupSettings(
        enabled=enabled,
        app_id=app_id,
        app_secret=app_secret,
        configuration_id=configuration_id,
        graph_api_version=graph_version,
        allowed_origin=allowed_origin,
        session_ttl_seconds=ttl,
    )


def reset_whatsapp_embedded_signup_settings_cache() -> None:
    get_whatsapp_embedded_signup_settings.cache_clear()


def _optional(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


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


def _int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def _secret(
    *,
    value_env: str,
    file_env: str,
    default_file: str,
    production: bool,
) -> str | None:
    configured = os.getenv(file_env, "").strip()
    path = Path(configured or default_file)
    if path.is_file() and not path.is_symlink():
        value = path.read_text(encoding="utf-8").strip()
        return value or None
    raw = os.getenv(value_env, "").strip()
    if raw and production:
        raise RuntimeError(
            f"production prohibits plain text {value_env}; use {file_env}"
        )
    return raw or None
