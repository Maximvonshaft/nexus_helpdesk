from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

_ALLOWED_AGENT_MODES = {"ai_first_human_fallback"}
_ALLOWED_STT_PROVIDERS = {"mock", "disabled", "contract_stub", "deepgram"}
_ALLOWED_TTS_PROVIDERS = {"mock", "disabled", "contract_stub"}
_ALLOWED_AI_PROVIDERS = {"provider_runtime"}
_ALLOWED_AUDIO_REFERENCE_SOURCES = {"disabled", "static_fixture"}
_ALLOWED_PARTICIPANT_MODES = {"fake_room_client"}
_LOCAL_AUDIO_REFERENCE_HOSTS = {"localhost", "127.0.0.1", "::1"}


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
    stt_timeout_ms: int
    tts_timeout_ms: int
    stt_contract_stub_enabled: bool
    tts_contract_stub_enabled: bool
    stt_token_file: str | None
    tts_token_file: str | None
    stt_inline_token: str | None
    tts_inline_token: str | None
    stt_canary_percent: int
    tts_canary_percent: int
    stt_deepgram_enabled: bool
    stt_deepgram_model: str
    stt_deepgram_smart_format: bool
    stt_deepgram_endpoint: str
    stt_deepgram_remote_url_allowlist: str | None
    audio_reference_source: str
    audio_reference_static_url: str | None
    audio_reference_allowlist: str | None
    audio_reference_static_enabled: bool
    participant_enabled: bool
    participant_mode: str
    participant_token_ttl_seconds: int
    participant_id_prefix: str
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
            raise RuntimeError("WEBCALL_STT_PROVIDER must be mock, disabled, contract_stub, or deepgram in PR-6")
        if self.tts_provider not in _ALLOWED_TTS_PROVIDERS:
            raise RuntimeError("WEBCALL_TTS_PROVIDER must be mock, disabled, or contract_stub in PR-5")
        if self.stt_provider == "contract_stub" and not self.stt_contract_stub_enabled:
            raise RuntimeError("WEBCALL_STT_CONTRACT_STUB_ENABLED must be true for contract_stub")
        if self.stt_provider == "deepgram" and not self.stt_deepgram_enabled:
            raise RuntimeError("WEBCALL_STT_DEEPGRAM_ENABLED must be true for deepgram")
        if self.tts_provider == "contract_stub" and not self.tts_contract_stub_enabled:
            raise RuntimeError("WEBCALL_TTS_CONTRACT_STUB_ENABLED must be true for contract_stub")
        if self.ai_provider not in _ALLOWED_AI_PROVIDERS:
            raise RuntimeError("WEBCALL_AI_PROVIDER must be provider_runtime in this foundation PR")
        if self.audio_reference_source not in _ALLOWED_AUDIO_REFERENCE_SOURCES:
            raise RuntimeError("WEBCALL_AI_AUDIO_REFERENCE_SOURCE must be disabled or static_fixture in PR-7")
        if self.app_env == "production" and self.audio_reference_source == "static_fixture":
            raise RuntimeError("WEBCALL_AI_AUDIO_REFERENCE_SOURCE static_fixture is not allowed in production")
        if self.audio_reference_source == "static_fixture":
            self._validate_static_audio_reference()
        if self.participant_mode not in _ALLOWED_PARTICIPANT_MODES:
            raise RuntimeError("WEBCALL_AI_PARTICIPANT_MODE must be fake_room_client in PR-8")
        if self.participant_token_ttl_seconds < 60 or self.participant_token_ttl_seconds > 900:
            raise RuntimeError("WEBCALL_AI_PARTICIPANT_TOKEN_TTL_SECONDS must be between 60 and 900")
        if not self.participant_id_prefix:
            raise RuntimeError("WEBCALL_AI_PARTICIPANT_ID_PREFIX must not be empty")
        if self.app_env == "production" and self.participant_enabled:
            raise RuntimeError("WEBCALL_AI_PARTICIPANT_ENABLED must be false in production for PR-8")
        if self.max_turns < 1 or self.max_turns > 12:
            raise RuntimeError("WEBCALL_AI_AGENT_MAX_TURNS must be between 1 and 12")
        if self.max_call_seconds < 30 or self.max_call_seconds > 600:
            raise RuntimeError("WEBCALL_AI_AGENT_MAX_CALL_SECONDS must be between 30 and 600")
        if self.stt_timeout_ms < 100 or self.stt_timeout_ms > 30000:
            raise RuntimeError("WEBCALL_STT_TIMEOUT_MS must be between 100 and 30000")
        if self.tts_timeout_ms < 100 or self.tts_timeout_ms > 30000:
            raise RuntimeError("WEBCALL_TTS_TIMEOUT_MS must be between 100 and 30000")
        if self.stt_canary_percent < 0 or self.stt_canary_percent > 100:
            raise RuntimeError("WEBCALL_STT_CANARY_PERCENT must be between 0 and 100")
        if self.tts_canary_percent < 0 or self.tts_canary_percent > 100:
            raise RuntimeError("WEBCALL_TTS_CANARY_PERCENT must be between 0 and 100")
        if not self.stt_deepgram_endpoint:
            raise RuntimeError("WEBCALL_STT_DEEPGRAM_ENDPOINT must not be empty")
        if self.stt_deepgram_model.strip() == "":
            raise RuntimeError("WEBCALL_STT_DEEPGRAM_MODEL must not be empty")
        if self.app_env == "production":
            if self.stt_inline_token:
                raise RuntimeError("WEBCALL_STT_TOKEN must not be set inline in production")
            if self.tts_inline_token:
                raise RuntimeError("WEBCALL_TTS_TOKEN must not be set inline in production")
            if self.allow_speedaf_work_order:
                raise RuntimeError("WEBCALL_AI_ALLOW_SPEEDAF_WORK_ORDER must be false in production for PR-1")
            if self.allow_cancel:
                raise RuntimeError("WEBCALL_AI_ALLOW_CANCEL must be false in production")
            if self.allow_address_update:
                raise RuntimeError("WEBCALL_AI_ALLOW_ADDRESS_UPDATE must be false in production")
            if self.record_raw_audio:
                raise RuntimeError("WEBCALL_AI_RECORD_RAW_AUDIO must be false in production")
            if self.stt_provider == "deepgram":
                if not self.stt_token_file:
                    raise RuntimeError("WEBCALL_STT_TOKEN_FILE is required for deepgram in production")
                if not self.stt_deepgram_endpoint.startswith("https://"):
                    raise RuntimeError("WEBCALL_STT_DEEPGRAM_ENDPOINT must be https in production")

    def _validate_static_audio_reference(self) -> None:
        if not self.audio_reference_static_enabled:
            raise RuntimeError("WEBCALL_AI_AUDIO_REFERENCE_STATIC_ENABLED must be true for static_fixture")
        if not self.audio_reference_static_url:
            raise RuntimeError("WEBCALL_AI_AUDIO_REFERENCE_STATIC_URL is required for static_fixture")
        if not self.audio_reference_static_url.startswith("https://"):
            raise RuntimeError("WEBCALL_AI_AUDIO_REFERENCE_STATIC_URL must be https")
        host = _host_from_https_url(self.audio_reference_static_url)
        if not host or host in _LOCAL_AUDIO_REFERENCE_HOSTS:
            raise RuntimeError("WEBCALL_AI_AUDIO_REFERENCE_STATIC_URL must use a non-local https host")
        if self.audio_reference_allowlist:
            allowed_hosts = _csv_hosts(self.audio_reference_allowlist)
            if host not in allowed_hosts:
                raise RuntimeError("WEBCALL_AI_AUDIO_REFERENCE_STATIC_URL host must be in allowlist")


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
        stt_timeout_ms=_env_int("WEBCALL_STT_TIMEOUT_MS", 5000, minimum=100, maximum=30000),
        tts_timeout_ms=_env_int("WEBCALL_TTS_TIMEOUT_MS", 5000, minimum=100, maximum=30000),
        stt_contract_stub_enabled=_env_bool("WEBCALL_STT_CONTRACT_STUB_ENABLED", False),
        tts_contract_stub_enabled=_env_bool("WEBCALL_TTS_CONTRACT_STUB_ENABLED", False),
        stt_token_file=os.getenv("WEBCALL_STT_TOKEN_FILE") or None,
        tts_token_file=os.getenv("WEBCALL_TTS_TOKEN_FILE") or None,
        stt_inline_token=os.getenv("WEBCALL_STT_TOKEN") or None,
        tts_inline_token=os.getenv("WEBCALL_TTS_TOKEN") or None,
        stt_canary_percent=_env_int("WEBCALL_STT_CANARY_PERCENT", 0, minimum=0, maximum=100),
        tts_canary_percent=_env_int("WEBCALL_TTS_CANARY_PERCENT", 0, minimum=0, maximum=100),
        stt_deepgram_enabled=_env_bool("WEBCALL_STT_DEEPGRAM_ENABLED", False),
        stt_deepgram_model=os.getenv("WEBCALL_STT_DEEPGRAM_MODEL", "nova-3").strip() or "nova-3",
        stt_deepgram_smart_format=_env_bool("WEBCALL_STT_DEEPGRAM_SMART_FORMAT", True),
        stt_deepgram_endpoint=os.getenv("WEBCALL_STT_DEEPGRAM_ENDPOINT", "https://api.deepgram.com/v1/listen").strip()
        or "https://api.deepgram.com/v1/listen",
        stt_deepgram_remote_url_allowlist=os.getenv("WEBCALL_STT_DEEPGRAM_REMOTE_URL_ALLOWLIST") or None,
        audio_reference_source=os.getenv("WEBCALL_AI_AUDIO_REFERENCE_SOURCE", "disabled").strip().lower()
        or "disabled",
        audio_reference_static_url=(os.getenv("WEBCALL_AI_AUDIO_REFERENCE_STATIC_URL") or "").strip() or None,
        audio_reference_allowlist=(os.getenv("WEBCALL_AI_AUDIO_REFERENCE_ALLOWLIST") or "").strip() or None,
        audio_reference_static_enabled=_env_bool("WEBCALL_AI_AUDIO_REFERENCE_STATIC_ENABLED", False),
        participant_enabled=_env_bool("WEBCALL_AI_PARTICIPANT_ENABLED", False),
        participant_mode=os.getenv("WEBCALL_AI_PARTICIPANT_MODE", "fake_room_client").strip().lower()
        or "fake_room_client",
        participant_token_ttl_seconds=_env_int(
            "WEBCALL_AI_PARTICIPANT_TOKEN_TTL_SECONDS",
            300,
            minimum=60,
            maximum=900,
        ),
        participant_id_prefix=os.getenv("WEBCALL_AI_PARTICIPANT_ID_PREFIX", "ai_webcall").strip()
        or "ai_webcall",
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


def _host_from_https_url(value: str) -> str:
    without_scheme = value[len("https://") :]
    authority = without_scheme.split("/", 1)[0]
    if authority.startswith("["):
        return authority.split("]", 1)[0][1:].lower()
    return authority.split(":", 1)[0].lower()


def _csv_hosts(value: str) -> set[str]:
    return {item.strip().lower() for item in value.split(",") if item.strip()}
