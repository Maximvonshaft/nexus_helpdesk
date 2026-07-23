from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

_ALLOWED_TURN_DETECTION = {"stt", "vad", "manual"}
_ALLOWED_HTTP_INTERNAL_HOSTS = {
    "app-controlled",
    "app",
    "localhost",
    "127.0.0.1",
    "::1",
}
_DEFAULT_AGENT_NAME = "nexus-voice-agent"


def _read_secret(name: str, file_name: str) -> str | None:
    inline = str(os.getenv(name) or "").strip()
    if inline:
        return inline
    path = str(os.getenv(file_name) or "").strip()
    if not path:
        return None
    try:
        return Path(path).read_text(encoding="utf-8").strip() or None
    except OSError as exc:
        raise RuntimeError(
            f"{file_name} is configured but cannot be read: {type(exc).__name__}"
        ) from exc


def _positive_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, str(default))).strip())
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def livekit_agent_registration_name() -> str:
    """Return a stable decorator name; full validation occurs at worker startup."""

    return str(os.getenv("LIVEKIT_AGENT_NAME") or _DEFAULT_AGENT_NAME).strip() or _DEFAULT_AGENT_NAME


@dataclass(frozen=True)
class LiveKitAgentWorkerConfig:
    agent_name: str
    shared_secret: str
    nexus_internal_api_url: str
    stt_model: str
    tts_model: str
    turn_detection: str
    request_timeout_seconds: int
    heartbeat_seconds: int
    greeting: str
    handoff_wait_message: str


def load_livekit_agent_worker_config() -> LiveKitAgentWorkerConfig:
    config = LiveKitAgentWorkerConfig(
        agent_name=str(os.getenv("LIVEKIT_AGENT_NAME") or "").strip(),
        shared_secret=str(
            _read_secret(
                "LIVEKIT_AGENT_SHARED_SECRET",
                "LIVEKIT_AGENT_SHARED_SECRET_FILE",
            )
            or ""
        ).strip(),
        nexus_internal_api_url=str(
            os.getenv("NEXUS_INTERNAL_API_URL") or "http://app-controlled:8080"
        ).strip().rstrip("/"),
        stt_model=str(os.getenv("NEXUS_VOICE_STT_MODEL") or "").strip(),
        tts_model=str(os.getenv("NEXUS_VOICE_TTS_MODEL") or "").strip(),
        turn_detection=str(
            os.getenv("NEXUS_VOICE_TURN_DETECTION") or "stt"
        ).strip().lower(),
        request_timeout_seconds=_positive_int(
            "NEXUS_VOICE_AGENT_REQUEST_TIMEOUT_SECONDS",
            30,
            minimum=3,
            maximum=120,
        ),
        heartbeat_seconds=_positive_int(
            "NEXUS_VOICE_AGENT_HEARTBEAT_SECONDS",
            30,
            minimum=10,
            maximum=120,
        ),
        greeting=str(
            os.getenv("NEXUS_VOICE_GREETING")
            or "Hello. How can I help you today?"
        ).strip()[:500],
        handoff_wait_message=str(
            os.getenv("NEXUS_VOICE_HANDOFF_WAIT_MESSAGE")
            or "Your request is waiting for a human support agent. Please stay on the line."
        ).strip()[:500],
    )
    validate_livekit_agent_worker_config(config)
    return config


def validate_livekit_agent_worker_config(config: LiveKitAgentWorkerConfig) -> None:
    if not config.agent_name:
        raise RuntimeError("LIVEKIT_AGENT_NAME is required")
    if not config.shared_secret:
        raise RuntimeError("LIVEKIT_AGENT_SHARED_SECRET is required")
    if not config.nexus_internal_api_url:
        raise RuntimeError("NEXUS_INTERNAL_API_URL is required")
    parsed = urlparse(config.nexus_internal_api_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError("NEXUS_INTERNAL_API_URL must be an absolute HTTP URL")
    app_env = str(os.getenv("APP_ENV") or "development").strip().lower()
    if (
        app_env == "production"
        and parsed.scheme != "https"
        and parsed.hostname not in _ALLOWED_HTTP_INTERNAL_HOSTS
    ):
        raise RuntimeError(
            "NEXUS_INTERNAL_API_URL must use HTTPS outside the controlled internal network"
        )
    if not config.stt_model:
        raise RuntimeError("NEXUS_VOICE_STT_MODEL is required")
    if not config.tts_model:
        raise RuntimeError("NEXUS_VOICE_TTS_MODEL is required")
    if config.turn_detection not in _ALLOWED_TURN_DETECTION:
        raise RuntimeError(
            "NEXUS_VOICE_TURN_DETECTION must be stt, vad, or manual"
        )
    if not config.greeting:
        raise RuntimeError("NEXUS_VOICE_GREETING must not be empty")
    if not config.handoff_wait_message:
        raise RuntimeError("NEXUS_VOICE_HANDOFF_WAIT_MESSAGE must not be empty")


def materialize_livekit_worker_credentials() -> None:
    """Expose file-backed credentials to the LiveKit Agents SDK process only."""

    for name, file_name in (
        ("LIVEKIT_API_KEY", "LIVEKIT_API_KEY_FILE"),
        ("LIVEKIT_API_SECRET", "LIVEKIT_API_SECRET_FILE"),
    ):
        value = _read_secret(name, file_name)
        if value:
            os.environ[name] = value
    if not str(os.getenv("LIVEKIT_URL") or "").strip():
        raise RuntimeError("LIVEKIT_URL is required")
    if not str(os.getenv("LIVEKIT_API_KEY") or "").strip():
        raise RuntimeError("LIVEKIT_API_KEY is required")
    if not str(os.getenv("LIVEKIT_API_SECRET") or "").strip():
        raise RuntimeError("LIVEKIT_API_SECRET is required")
