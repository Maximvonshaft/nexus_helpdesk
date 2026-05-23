from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

_ALLOWED_AGENT_MODES = {"ai_first_human_fallback"}
_ALLOWED_STT_PROVIDERS = {"mock"}
_ALLOWED_TTS_PROVIDERS = {"mock"}
_ALLOWED_AI_PROVIDERS = {"provider_runtime"}


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    if value < minimum:
        return minimum
    if value > maximum:
        return maximum
    return value


@dataclass(frozen=True)
class WebCallAISettings:
    enabled: bool
    agent_mode: str
    max_turns: int
    max_call_seconds: int
    stt_provider: str
    tts_provider: str
    ai_provider: str
    allow_speedaf_work_order: bool
    allow_cancel: bool
    allow_address_update: bool
    transcript_enabled: bool
    summary_enabled: bool
    record_raw_audio: bool
    app_env: str

    def validate_runtime(self) -> None:
        if self.agent_mode not in _ALLOWED_AGENT_MODES:
            raise RuntimeError("WEBCALL_AI_AGENT_MODE must be ai_first_human_fallback")
        if self.stt_provider not in _ALLOWED_STT_PROVIDERS:
            raise RuntimeError("WEBCALL_STT_PROVIDER must be mock in this foundation PR")
        if self.tts_provider not in _ALLOWED_TTS_PROVIDERS:
            raise RuntimeError("WEBCALL_TTS_PROVIDER must be mock in this foundation PR")
        if self.ai_provider not in _ALLOWED_AI_PROVIDERS:
            raise RuntimeError("WEBCALL_AI_PROVIDER must be provider_runtime in this foundation PR")
        if self.max_turns < 1 or self.max_turns > 12:
            raise RuntimeError("WEBCALL_AI_AGENT_MAX_TURNS must be between 1 and 12")
        if self.max_call_seconds < 30 or self.max_call_seconds > 600:
            raise RuntimeError("WEBCALL_AI_AGENT_MAX_CALL_SECONDS must be between 30 and 600")
        if self.app_env == "production":
            if self.allow_speedaf_work_order:
                raise RuntimeError("WEBCALL_AI_ALLOW_SPEEDAF_WORK_ORDER must be false in production for PR-1")
            if self.allow_cancel:
                raise RuntimeError("WEBCALL_AI_ALLOW_CANCEL must be false in production")
            if self.allow_address_update:
                raise RuntimeError("WEBCALL_AI_ALLOW_ADDRESS_UPDATE must be false in production")
            if self.record_raw_audio:
                raise RuntimeError("WEBCALL_AI_RECORD_RAW_AUDIO must be false in production")


@lru_cache(maxsize=1)
def get_webcall_ai_settings() -> WebCallAISettings:
    settings = WebCallAISettings(
        enabled=_env_bool("WEBCALL_AI_AGENT_ENABLED", False),
        agent_mode=os.getenv("WEBCALL_AI_AGENT_MODE", "ai_first_human_fallback").strip().lower()
        or "ai_first_human_fallback",
        max_turns=_env_int("WEBCALL_AI_AGENT_MAX_TURNS", 6, minimum=1, maximum=12),
        max_call_seconds=_env_int("WEBCALL_AI_AGENT_MAX_CALL_SECONDS", 180, minimum=30, maximum=600),
        stt_provider=os.getenv("WEBCALL_STT_PROVIDER", "mock").strip().lower() or "mock",
        tts_provider=os.getenv("WEBCALL_TTS_PROVIDER", "mock").strip().lower() or "mock",
        ai_provider=os.getenv("WEBCALL_AI_PROVIDER", "provider_runtime").strip().lower() or "provider_runtime",
        allow_speedaf_work_order=_env_bool("WEBCALL_AI_ALLOW_SPEEDAF_WORK_ORDER", False),
        allow_cancel=_env_bool("WEBCALL_AI_ALLOW_CANCEL", False),
        allow_address_update=_env_bool("WEBCALL_AI_ALLOW_ADDRESS_UPDATE", False),
        transcript_enabled=_env_bool("WEBCALL_AI_TRANSCRIPT_ENABLED", True),
        summary_enabled=_env_bool("WEBCALL_AI_SUMMARY_ENABLED", False),
        record_raw_audio=_env_bool("WEBCALL_AI_RECORD_RAW_AUDIO", False),
        app_env=os.getenv("APP_ENV", "development").strip().lower() or "development",
    )
    settings.validate_runtime()
    return settings
