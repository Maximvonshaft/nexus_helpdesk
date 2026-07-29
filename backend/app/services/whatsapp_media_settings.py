from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


MEDIA_KIND_LIMITS: dict[str, int] = {
    "image": 5 * 1024 * 1024,
    "audio": 16 * 1024 * 1024,
    "video": 16 * 1024 * 1024,
    "document": 100 * 1024 * 1024,
    "sticker": 500 * 1024,
}

MEDIA_KIND_MIME_PREFIXES: dict[str, tuple[str, ...]] = {
    "image": ("image/jpeg", "image/png", "image/webp"),
    "audio": (
        "audio/aac",
        "audio/amr",
        "audio/mpeg",
        "audio/mp4",
        "audio/ogg",
        "audio/opus",
    ),
    "video": ("video/mp4", "video/3gpp"),
    "document": (
        "application/pdf",
        "application/msword",
        "application/vnd.ms-excel",
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "text/plain",
        "text/csv",
    ),
    "sticker": ("image/webp",),
}


@dataclass(frozen=True)
class WhatsAppMediaSettings:
    enabled: bool
    scanner: str
    clamav_host: str
    clamav_port: int
    clamav_timeout_seconds: int
    max_total_bytes: int


@lru_cache(maxsize=1)
def get_whatsapp_media_settings() -> WhatsAppMediaSettings:
    app_env = os.getenv("APP_ENV", "development").strip().lower()
    enabled = _bool("WHATSAPP_MEDIA_ENABLED", False)
    scanner = os.getenv("WHATSAPP_MEDIA_SCANNER", "disabled").strip().lower()
    if scanner not in {"disabled", "clamav"}:
        raise RuntimeError("WHATSAPP_MEDIA_SCANNER must be disabled or clamav")
    if enabled and app_env == "production" and scanner != "clamav":
        raise RuntimeError(
            "production WhatsApp media requires WHATSAPP_MEDIA_SCANNER=clamav"
        )
    host = os.getenv("WHATSAPP_CLAMAV_HOST", "127.0.0.1").strip()
    if not host or any(char in host for char in "\r\n\x00/"):
        raise RuntimeError("WHATSAPP_CLAMAV_HOST is invalid")
    port = _int("WHATSAPP_CLAMAV_PORT", 3310, 1, 65535)
    timeout = _int("WHATSAPP_CLAMAV_TIMEOUT_SECONDS", 20, 1, 120)
    total = _int(
        "WHATSAPP_MEDIA_MAX_TOTAL_BYTES",
        MEDIA_KIND_LIMITS["document"],
        1024,
        MEDIA_KIND_LIMITS["document"],
    )
    return WhatsAppMediaSettings(
        enabled=enabled,
        scanner=scanner,
        clamav_host=host,
        clamav_port=port,
        clamav_timeout_seconds=timeout,
        max_total_bytes=total,
    )


def reset_whatsapp_media_settings_cache() -> None:
    get_whatsapp_media_settings.cache_clear()


def max_bytes_for_kind(kind: str) -> int:
    normalized = str(kind or "").strip().lower()
    if normalized not in MEDIA_KIND_LIMITS:
        raise ValueError("unsupported_whatsapp_media_kind")
    settings = get_whatsapp_media_settings()
    return min(MEDIA_KIND_LIMITS[normalized], settings.max_total_bytes)


def allowed_mime_types_for_kind(kind: str) -> set[str]:
    normalized = str(kind or "").strip().lower()
    values = MEDIA_KIND_MIME_PREFIXES.get(normalized)
    if values is None:
        raise ValueError("unsupported_whatsapp_media_kind")
    return set(values)


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
