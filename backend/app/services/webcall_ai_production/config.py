from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


TRUE_VALUES = {"1", "true", "yes", "on"}
PROVIDER_PROFILES = {"fake", "external"}
ROLLOUT_MODES = {"off", "internal", "canary", "public"}


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in TRUE_VALUES


def _int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        value = default
    else:
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer") from exc
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _provider_env(name: str, default: str = "fake") -> str:
    value = (os.getenv(name) or default).strip().lower()
    if not value:
        return default
    return value


def _secret_configured(name: str, file_name: str) -> bool:
    return bool((os.getenv(name) or "").strip() or (os.getenv(file_name) or "").strip())


@dataclass(frozen=True)
class WebCallAIProductionSettings:
    production_enabled: bool
    agent_enabled: bool
    kill_switch: bool
    public_rollout_mode: str
    allowed_origins: tuple[str, ...]
    agent_lease_seconds: int
    provider_profile: str
    max_active_sessions: int
    max_turns_per_session: int
    max_session_seconds: int
    record_raw_audio: bool
    allow_speedaf_work_order: bool
    allow_cancel: bool
    allow_address_update: bool
    webchat_voice_provider: str
    webchat_voice_enabled: bool
    livekit_url: str | None
    livekit_api_key_configured: bool
    livekit_api_secret_configured: bool
    stt_provider: str
    llm_provider: str
    tts_provider: str
    external_stt_configured: bool
    external_llm_configured: bool
    external_tts_configured: bool

    @property
    def livekit_configured(self) -> bool:
        return bool(self.livekit_url and self.livekit_api_key_configured and self.livekit_api_secret_configured)

    @property
    def provider_configured(self) -> bool:
        if self.provider_profile == "fake":
            return self.stt_provider == self.llm_provider == self.tts_provider == "fake"
        return self.external_stt_configured and self.external_llm_configured and self.external_tts_configured

    @property
    def status(self) -> str:
        if self.kill_switch:
            return "kill_switch"
        if not self.production_enabled or self.public_rollout_mode == "off":
            return "disabled"
        if self.webchat_voice_provider == "livekit" and not self.livekit_configured:
            return "misconfigured"
        if not self.provider_configured:
            return "provider_misconfigured"
        return "ready"

    def validate(self) -> None:
        app_env = (os.getenv("APP_ENV") or "development").strip().lower()
        if self.provider_profile not in PROVIDER_PROFILES:
            raise ValueError("WEBCALL_AI_PROVIDER_PROFILE must be fake or external")
        if self.public_rollout_mode not in ROLLOUT_MODES:
            raise ValueError("WEBCALL_AI_PUBLIC_ROLLOUT_MODE must be off, internal, canary, or public")
        if self.record_raw_audio:
            raise ValueError("WEBCALL_AI_RECORD_RAW_AUDIO=true is not supported by default")
        if self.allow_speedaf_work_order or self.allow_cancel or self.allow_address_update:
            raise ValueError("high-risk WebCall AI write actions are not supported in the infrastructure skeleton")
        if self.production_enabled and self.webchat_voice_provider == "livekit" and not self.livekit_configured:
            raise ValueError("LiveKit production WebCall AI requires LIVEKIT_URL, LIVEKIT_API_KEY, and LIVEKIT_API_SECRET")
        if self.provider_profile == "fake" and any(provider != "fake" for provider in [self.stt_provider, self.llm_provider, self.tts_provider]):
            raise ValueError("fake provider profile requires STT_PROVIDER, LLM_PROVIDER, and TTS_PROVIDER to be fake")
        if self.provider_profile == "external" and any(provider != "external" for provider in [self.stt_provider, self.llm_provider, self.tts_provider]):
            raise ValueError("external provider profile requires STT_PROVIDER, LLM_PROVIDER, and TTS_PROVIDER to be external")
        if self.production_enabled and self.provider_profile == "fake" and self.public_rollout_mode in {"canary", "public"}:
            raise ValueError("fake provider profile cannot be used for canary or public WebCall AI rollout")
        if self.production_enabled and self.agent_enabled and self.provider_profile == "external" and not self.provider_configured:
            raise ValueError("external WebCall AI providers require STT, LLM, and TTS provider configuration")
        if app_env == "production":
            for name in ["STT_API_KEY", "LLM_API_KEY", "TTS_API_KEY", "LIVEKIT_API_SECRET"]:
                if (os.getenv(name) or "").strip():
                    raise ValueError(f"{name} must not be configured inline in production; use the matching *_FILE secret")

    def public_runtime_config(self) -> dict[str, object]:
        return {
            "enabled": self.production_enabled,
            "agent_enabled": self.agent_enabled and not self.kill_switch,
            "kill_switch": self.kill_switch,
            "rollout_mode": self.public_rollout_mode,
            "status": self.status,
            "provider_profile": self.provider_profile,
            "voice_provider": self.webchat_voice_provider,
            "livekit_url": self.livekit_url if self.webchat_voice_provider == "livekit" else None,
            "max_session_seconds": self.max_session_seconds,
            "record_raw_audio": False,
            "stt_provider": self.stt_provider,
            "llm_provider": self.llm_provider,
            "tts_provider": self.tts_provider,
        }


@lru_cache(maxsize=1)
def get_webcall_ai_production_settings() -> WebCallAIProductionSettings:
    settings = WebCallAIProductionSettings(
        production_enabled=_bool_env("WEBCALL_AI_PRODUCTION_ENABLED", False),
        agent_enabled=_bool_env("WEBCALL_AI_AGENT_ENABLED", False),
        kill_switch=_bool_env("WEBCALL_AI_KILL_SWITCH", False),
        public_rollout_mode=_provider_env("WEBCALL_AI_PUBLIC_ROLLOUT_MODE", "internal"),
        allowed_origins=tuple(item.strip() for item in (os.getenv("WEBCALL_AI_ALLOWED_ORIGINS") or "").split(",") if item.strip()),
        agent_lease_seconds=_int_env("WEBCALL_AI_AGENT_LEASE_SECONDS", 45, minimum=5, maximum=300),
        provider_profile=_provider_env("WEBCALL_AI_PROVIDER_PROFILE", "fake"),
        max_active_sessions=_int_env("WEBCALL_AI_MAX_ACTIVE_SESSIONS", 3, minimum=1, maximum=100),
        max_turns_per_session=_int_env("WEBCALL_AI_MAX_TURNS_PER_SESSION", 10, minimum=1, maximum=100),
        max_session_seconds=_int_env("WEBCALL_AI_MAX_SESSION_SECONDS", 600, minimum=60, maximum=3600),
        record_raw_audio=_bool_env("WEBCALL_AI_RECORD_RAW_AUDIO", False),
        allow_speedaf_work_order=_bool_env("WEBCALL_AI_ALLOW_SPEEDAF_WORK_ORDER", False),
        allow_cancel=_bool_env("WEBCALL_AI_ALLOW_CANCEL", False),
        allow_address_update=_bool_env("WEBCALL_AI_ALLOW_ADDRESS_UPDATE", False),
        webchat_voice_provider=_provider_env("WEBCHAT_VOICE_PROVIDER", "mock"),
        webchat_voice_enabled=_bool_env("WEBCHAT_VOICE_ENABLED", False),
        livekit_url=(os.getenv("LIVEKIT_URL") or "").strip() or None,
        livekit_api_key_configured=_secret_configured("LIVEKIT_API_KEY", "LIVEKIT_API_KEY_FILE"),
        livekit_api_secret_configured=_secret_configured("LIVEKIT_API_SECRET", "LIVEKIT_API_SECRET_FILE"),
        stt_provider=_provider_env("STT_PROVIDER", "fake"),
        llm_provider=_provider_env("LLM_PROVIDER", "fake"),
        tts_provider=_provider_env("TTS_PROVIDER", "fake"),
        external_stt_configured=bool((os.getenv("STT_API_KEY_FILE") or "").strip() and (os.getenv("STT_ENDPOINT") or "").strip()),
        external_llm_configured=bool((os.getenv("LLM_API_KEY_FILE") or "").strip() and (os.getenv("LLM_ENDPOINT") or "").strip()),
        external_tts_configured=bool((os.getenv("TTS_API_KEY_FILE") or "").strip() and (os.getenv("TTS_ENDPOINT") or "").strip()),
    )
    settings.validate()
    return settings
