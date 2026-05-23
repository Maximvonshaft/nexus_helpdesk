import os

import pytest

from app.services.webcall_ai.config import get_webcall_ai_settings


WEBCALL_ENV_KEYS = [
    "APP_ENV",
    "WEBCALL_AI_AGENT_ENABLED",
    "WEBCALL_AI_AGENT_MODE",
    "WEBCALL_AI_AGENT_MAX_TURNS",
    "WEBCALL_AI_AGENT_MAX_CALL_SECONDS",
    "WEBCALL_STT_PROVIDER",
    "WEBCALL_TTS_PROVIDER",
    "WEBCALL_STT_TIMEOUT_MS",
    "WEBCALL_TTS_TIMEOUT_MS",
    "WEBCALL_STT_CONTRACT_STUB_ENABLED",
    "WEBCALL_TTS_CONTRACT_STUB_ENABLED",
    "WEBCALL_STT_TOKEN_FILE",
    "WEBCALL_TTS_TOKEN_FILE",
    "WEBCALL_STT_TOKEN",
    "WEBCALL_TTS_TOKEN",
    "WEBCALL_STT_CANARY_PERCENT",
    "WEBCALL_TTS_CANARY_PERCENT",
    "WEBCALL_STT_DEEPGRAM_ENABLED",
    "WEBCALL_STT_DEEPGRAM_MODEL",
    "WEBCALL_STT_DEEPGRAM_SMART_FORMAT",
    "WEBCALL_STT_DEEPGRAM_ENDPOINT",
    "WEBCALL_STT_DEEPGRAM_REMOTE_URL_ALLOWLIST",
    "WEBCALL_AI_AUDIO_REFERENCE_SOURCE",
    "WEBCALL_AI_AUDIO_REFERENCE_STATIC_URL",
    "WEBCALL_AI_AUDIO_REFERENCE_ALLOWLIST",
    "WEBCALL_AI_AUDIO_REFERENCE_STATIC_ENABLED",
    "WEBCALL_AI_PARTICIPANT_ENABLED",
    "WEBCALL_AI_PARTICIPANT_MODE",
    "WEBCALL_AI_PARTICIPANT_TOKEN_TTL_SECONDS",
    "WEBCALL_AI_PARTICIPANT_ID_PREFIX",
    "WEBCALL_AI_PROVIDER",
    "WEBCALL_AI_ALLOW_SPEEDAF_WORK_ORDER",
    "WEBCALL_AI_ALLOW_CANCEL",
    "WEBCALL_AI_ALLOW_ADDRESS_UPDATE",
    "WEBCALL_AI_TRANSCRIPT_ENABLED",
    "WEBCALL_AI_SUMMARY_ENABLED",
    "WEBCALL_AI_RECORD_RAW_AUDIO",
]


@pytest.fixture(autouse=True)
def clean_webcall_ai_env(monkeypatch):
    get_webcall_ai_settings.cache_clear()
    for key in WEBCALL_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield
    get_webcall_ai_settings.cache_clear()


def test_webcall_ai_defaults_are_disabled_and_mock():
    settings = get_webcall_ai_settings()

    assert settings.enabled is False
    assert settings.agent_mode == "ai_first_human_fallback"
    assert settings.max_turns == 6
    assert settings.max_call_seconds == 180
    assert settings.stt_provider == "mock"
    assert settings.tts_provider == "mock"
    assert settings.stt_timeout_ms == 5000
    assert settings.tts_timeout_ms == 5000
    assert settings.stt_contract_stub_enabled is False
    assert settings.tts_contract_stub_enabled is False
    assert settings.stt_token_file is None
    assert settings.tts_token_file is None
    assert settings.stt_inline_token is None
    assert settings.tts_inline_token is None
    assert settings.stt_canary_percent == 0
    assert settings.tts_canary_percent == 0
    assert settings.stt_deepgram_enabled is False
    assert settings.stt_deepgram_model == "nova-3"
    assert settings.stt_deepgram_smart_format is True
    assert settings.stt_deepgram_endpoint == "https://api.deepgram.com/v1/listen"
    assert settings.stt_deepgram_remote_url_allowlist is None
    assert settings.audio_reference_source == "disabled"
    assert settings.audio_reference_static_url is None
    assert settings.audio_reference_allowlist is None
    assert settings.audio_reference_static_enabled is False
    assert settings.participant_enabled is False
    assert settings.participant_mode == "fake_room_client"
    assert settings.participant_token_ttl_seconds == 300
    assert settings.participant_id_prefix == "ai_webcall"
    assert settings.ai_provider == "provider_runtime"
    assert settings.allow_speedaf_work_order is False
    assert settings.allow_cancel is False
    assert settings.allow_address_update is False
    assert settings.transcript_enabled is True
    assert settings.summary_enabled is False
    assert settings.record_raw_audio is False


@pytest.mark.parametrize(
    "flag",
    [
        "WEBCALL_AI_ALLOW_SPEEDAF_WORK_ORDER",
        "WEBCALL_AI_ALLOW_CANCEL",
        "WEBCALL_AI_ALLOW_ADDRESS_UPDATE",
        "WEBCALL_AI_RECORD_RAW_AUDIO",
    ],
)
def test_production_rejects_foundation_forbidden_flags(monkeypatch, flag):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv(flag, "true")
    get_webcall_ai_settings.cache_clear()

    with pytest.raises(RuntimeError, match=flag):
        get_webcall_ai_settings()


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("WEBCALL_STT_PROVIDER", "azure", "WEBCALL_STT_PROVIDER"),
        ("WEBCALL_TTS_PROVIDER", "elevenlabs", "WEBCALL_TTS_PROVIDER"),
        ("WEBCALL_AI_PROVIDER", "openai_responses", "WEBCALL_AI_PROVIDER"),
    ],
)
def test_invalid_provider_names_fail_closed(monkeypatch, key, value, message):
    monkeypatch.setenv(key, value)
    get_webcall_ai_settings.cache_clear()

    with pytest.raises(RuntimeError, match=message):
        get_webcall_ai_settings()


def test_max_turns_and_max_call_seconds_are_bounded(monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_AGENT_MAX_TURNS", "999")
    monkeypatch.setenv("WEBCALL_AI_AGENT_MAX_CALL_SECONDS", "9999")
    get_webcall_ai_settings.cache_clear()

    settings = get_webcall_ai_settings()

    assert settings.max_turns == 12
    assert settings.max_call_seconds == 600


def test_invalid_agent_mode_fails_closed(monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_AGENT_MODE", "ai_only")
    get_webcall_ai_settings.cache_clear()

    with pytest.raises(RuntimeError, match="WEBCALL_AI_AGENT_MODE"):
        get_webcall_ai_settings()


def test_participant_mode_allows_fake_room_client_only(monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_PARTICIPANT_MODE", "real_room_client")
    get_webcall_ai_settings.cache_clear()

    with pytest.raises(RuntimeError, match="WEBCALL_AI_PARTICIPANT_MODE"):
        get_webcall_ai_settings()


def test_production_rejects_participant_enabled(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WEBCALL_AI_PARTICIPANT_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    with pytest.raises(RuntimeError, match="WEBCALL_AI_PARTICIPANT_ENABLED"):
        get_webcall_ai_settings()


def test_no_test_leaks_webcall_ai_environment():
    assert not any(key for key in WEBCALL_ENV_KEYS if key != "APP_ENV" and key in os.environ)
