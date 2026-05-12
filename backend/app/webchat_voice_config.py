from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_csv(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _parse_sources(raw: str) -> list[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.replace(",", " ").split() if item.strip()]


def _normalize_url(raw: str | None) -> str | None:
    value = (raw or "").strip().rstrip("/")
    return value or None


def _livekit_wss_source(livekit_url: str | None) -> str | None:
    if not livekit_url:
        return None
    parsed = urlparse(livekit_url)
    if parsed.scheme == "wss":
        return livekit_url.rstrip("/")
    if parsed.scheme == "https":
        return urlunparse(parsed._replace(scheme="wss")).rstrip("/")
    return None


@dataclass(frozen=True)
class WebchatVoiceRuntimeConfig:
    enabled: bool
    allowed_path_prefixes: tuple[str, ...]
    connect_src: tuple[str, ...]
    provider: str
    session_ttl_seconds: int
    max_active_per_conversation: int
    rate_limit_window_seconds: int
    rate_limit_max_requests: int
    recording_enabled: bool
    transcription_enabled: bool
    livekit_url: str | None
    livekit_api_key: str | None
    livekit_api_secret: str | None


def load_webchat_voice_runtime_config() -> WebchatVoiceRuntimeConfig:
    config = WebchatVoiceRuntimeConfig(
        enabled=_env_bool("WEBCHAT_VOICE_ENABLED", False),
        allowed_path_prefixes=tuple(_parse_csv(os.getenv("WEBCHAT_VOICE_ALLOWED_PATH_PREFIXES", "/webchat/voice,/webcall"))),
        connect_src=tuple(_parse_sources(os.getenv("WEBCHAT_VOICE_CONNECT_SRC", ""))),
        provider=(os.getenv("WEBCHAT_VOICE_PROVIDER", "mock").strip().lower() or "mock"),
        session_ttl_seconds=int(os.getenv("WEBCHAT_VOICE_SESSION_TTL_SECONDS", "900")),
        max_active_per_conversation=int(os.getenv("WEBCHAT_VOICE_MAX_ACTIVE_PER_CONVERSATION", "1")),
        rate_limit_window_seconds=int(os.getenv("WEBCHAT_VOICE_RATE_LIMIT_WINDOW_SECONDS", "60")),
        rate_limit_max_requests=int(os.getenv("WEBCHAT_VOICE_RATE_LIMIT_MAX_REQUESTS", "5")),
        recording_enabled=_env_bool("WEBCHAT_VOICE_RECORDING_ENABLED", False),
        transcription_enabled=_env_bool("WEBCHAT_VOICE_TRANSCRIPTION_ENABLED", False),
        livekit_url=_normalize_url(os.getenv("LIVEKIT_URL")),
        livekit_api_key=(os.getenv("LIVEKIT_API_KEY") or "").strip() or None,
        livekit_api_secret=(os.getenv("LIVEKIT_API_SECRET") or "").strip() or None,
    )
    validate_webchat_voice_runtime_config(config)
    return config


def validate_webchat_voice_runtime_config(config: WebchatVoiceRuntimeConfig) -> None:
    if config.provider not in {"mock", "livekit"}:
        raise RuntimeError("WEBCHAT_VOICE_PROVIDER must be mock or livekit")
    if config.session_ttl_seconds < 60 or config.session_ttl_seconds > 3600:
        raise RuntimeError("WEBCHAT_VOICE_SESSION_TTL_SECONDS must be between 60 and 3600")
    if config.max_active_per_conversation < 1:
        raise RuntimeError("WEBCHAT_VOICE_MAX_ACTIVE_PER_CONVERSATION must be at least 1")
    if config.rate_limit_window_seconds < 10 or config.rate_limit_window_seconds > 3600:
        raise RuntimeError("WEBCHAT_VOICE_RATE_LIMIT_WINDOW_SECONDS must be between 10 and 3600")
    if config.rate_limit_max_requests < 1 or config.rate_limit_max_requests > 60:
        raise RuntimeError("WEBCHAT_VOICE_RATE_LIMIT_MAX_REQUESTS must be between 1 and 60")
    if config.enabled and not config.allowed_path_prefixes:
        raise RuntimeError("WEBCHAT_VOICE_ALLOWED_PATH_PREFIXES must be set when WEBCHAT_VOICE_ENABLED=true")
    for prefix in config.allowed_path_prefixes:
        if not prefix.startswith("/"):
            raise RuntimeError("WEBCHAT_VOICE_ALLOWED_PATH_PREFIXES entries must start with /")
    for source in config.connect_src:
        if "*" in source:
            raise RuntimeError("WEBCHAT_VOICE_CONNECT_SRC must not contain wildcard sources")
        normalized = source.strip().lower().strip("'")
        if normalized == "self":
            continue
        if not (normalized.startswith("https://") or normalized.startswith("wss://")):
            raise RuntimeError("WEBCHAT_VOICE_CONNECT_SRC entries must be https://, wss://, or self")
    app_env = os.getenv("APP_ENV", "development").strip().lower()
    if app_env == "production" and config.recording_enabled:
        raise RuntimeError("WEBCHAT_VOICE_RECORDING_ENABLED must remain false in production until a consent policy is implemented")
    if config.provider == "livekit":
        _validate_livekit_runtime_config(config, app_env=app_env)


def _validate_livekit_runtime_config(config: WebchatVoiceRuntimeConfig, *, app_env: str) -> None:
    if not config.livekit_url:
        raise RuntimeError("LIVEKIT_URL must be set when WEBCHAT_VOICE_PROVIDER=livekit")
    if not config.livekit_api_key:
        raise RuntimeError("LIVEKIT_API_KEY must be set when WEBCHAT_VOICE_PROVIDER=livekit")
    if not config.livekit_api_secret:
        raise RuntimeError("LIVEKIT_API_SECRET must be set when WEBCHAT_VOICE_PROVIDER=livekit")
    parsed = urlparse(config.livekit_url)
    if parsed.scheme not in {"wss", "ws", "https", "http"} or not parsed.netloc:
        raise RuntimeError("LIVEKIT_URL must be a valid LiveKit URL")
    if app_env == "production" and parsed.scheme != "wss":
        raise RuntimeError("LIVEKIT_URL must use wss:// in production")
    required_wss = _livekit_wss_source(config.livekit_url)
    if required_wss:
        normalized_sources = {source.rstrip("/") for source in config.connect_src}
        if required_wss not in normalized_sources:
            raise RuntimeError("WEBCHAT_VOICE_CONNECT_SRC must include the LiveKit wss URL when WEBCHAT_VOICE_PROVIDER=livekit")


def is_webchat_voice_path(path: str, config: WebchatVoiceRuntimeConfig | None = None) -> bool:
    runtime_config = config or load_webchat_voice_runtime_config()
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in runtime_config.allowed_path_prefixes)


def webchat_voice_connect_sources(config: WebchatVoiceRuntimeConfig | None = None) -> list[str]:
    runtime_config = config or load_webchat_voice_runtime_config()
    sources = []
    for source in runtime_config.connect_src:
        if source.strip().lower().strip("'") == "self":
            continue
        sources.append(source)
    return sources
