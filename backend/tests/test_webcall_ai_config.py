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
    "WEBCALL_AI_LIVEKIT_TOKEN_ISSUER_ENABLED",
    "WEBCALL_AI_ROOM_PRESENCE_ENABLED",
    "WEBCALL_AI_ROOM_PRESENCE_MODE",
    "WEBCALL_AI_ROOM_PRESENCE_JOIN_TIMEOUT_MS",
    "WEBCALL_AI_ROOM_PRESENCE_SMOKE_ENABLED",
    "WEBCALL_AI_STT_RUNTIME_ENABLED",
    "WEBCALL_AI_STT_RUNTIME_MODE",
    "WEBCALL_AI_STT_TRANSCRIPT_WRITE_ENABLED",
    "WEBCALL_AI_STT_TRANSCRIPT_PROVIDER_SESSION_ID_SOURCE",
    "WEBCALL_AI_ORCHESTRATOR_ENABLED",
    "WEBCALL_AI_ORCHESTRATOR_MODE",
    "WEBCALL_AI_TRACKING_LOOKUP_ENABLED",
    "WEBCALL_AI_TRACKING_REPLY_ENABLED",
    "WEBCALL_AI_TRACKING_COUNTRY_CODE",
    "WEBCALL_AI_TRACKING_LOOKUP_TIMEOUT_MS",
    "WEBCALL_AI_TTS_RUNTIME_ENABLED",
    "WEBCALL_AI_TTS_RUNTIME_MODE",
    "WEBCALL_AI_VOICE_EGRESS_ENABLED",
    "WEBCALL_AI_VOICE_EGRESS_MODE",
    "WEBCALL_AI_VOICE_EGRESS_SMOKE_ENABLED",
    "WEBCALL_AI_PILOT_CLOSURE_ENABLED",
    "WEBCALL_AI_PILOT_MODE",
    "WEBCALL_AI_PILOT_KILL_SWITCH",
    "WEBCALL_AI_PILOT_INTERNAL_ONLY",
    "WEBCALL_AI_PILOT_SESSION_ALLOWLIST",
    "WEBCALL_AI_PILOT_TENANT_ALLOWLIST",
    "WEBCALL_AI_PILOT_CANARY_PERCENT",
    "WEBCALL_AI_PILOT_EVIDENCE_ENABLED",
    "WEBCALL_AI_PILOT_HANDOFF_ENABLED",
    "WEBCALL_AI_PILOT_REAL_MEDIA_ENABLED",
    "WEBCALL_AI_PILOT_FAKE_TRACKING_ENABLED",
    "WEBCALL_AI_PILOT_FIXTURE_ENABLED",
    "WEBCALL_AI_PILOT_FIXTURE_ALLOW_DB_WRITE",
    "WEBCALL_AI_PILOT_SESSION_PUBLIC_ID",
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
    assert settings.livekit_token_issuer_enabled is False
    assert settings.room_presence_enabled is False
    assert settings.room_presence_mode == "fake_no_media"
    assert settings.room_presence_join_timeout_ms == 5000
    assert settings.room_presence_smoke_enabled is False
    assert settings.stt_runtime_enabled is False
    assert settings.stt_runtime_mode == "mock_text"
    assert settings.stt_transcript_write_enabled is False
    assert settings.stt_transcript_provider_session_id_source == "voice_session_public_id"
    assert settings.orchestrator_enabled is False
    assert settings.orchestrator_mode == "deterministic_tracking"
    assert settings.tracking_lookup_enabled is False
    assert settings.tracking_reply_enabled is False
    assert settings.tracking_country_code == "CH"
    assert settings.tracking_lookup_timeout_ms == 8000
    assert settings.tts_runtime_enabled is False
    assert settings.tts_runtime_mode == "mock_audio_reference"
    assert settings.voice_egress_enabled is False
    assert settings.voice_egress_mode == "fake_audio_reference"
    assert settings.voice_egress_smoke_enabled is False
    assert settings.pilot_closure_enabled is False
    assert settings.pilot_mode == "simulated_full_loop"
    assert settings.pilot_kill_switch is True
    assert settings.pilot_internal_only is True
    assert settings.pilot_session_allowlist is None
    assert settings.pilot_tenant_allowlist is None
    assert settings.pilot_canary_percent == 0
    assert settings.pilot_evidence_enabled is False
    assert settings.pilot_handoff_enabled is False
    assert settings.pilot_real_media_enabled is False
    assert settings.pilot_fake_tracking_enabled is False
    assert settings.pilot_fixture_enabled is False
    assert settings.pilot_fixture_allow_db_write is False
    assert settings.pilot_session_public_id is None
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
        ("WEBCALL_AI_PROVIDER", "legacy_provider", "WEBCALL_AI_PROVIDER"),
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


def test_livekit_token_issuer_requires_enable_flag(monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_PARTICIPANT_MODE", "livekit_token_issuer")
    get_webcall_ai_settings.cache_clear()

    with pytest.raises(RuntimeError, match="WEBCALL_AI_LIVEKIT_TOKEN_ISSUER_ENABLED"):
        get_webcall_ai_settings()


def test_livekit_token_issuer_mode_allowed_when_enabled(monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_PARTICIPANT_MODE", "livekit_token_issuer")
    monkeypatch.setenv("WEBCALL_AI_LIVEKIT_TOKEN_ISSUER_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    settings = get_webcall_ai_settings()

    assert settings.participant_mode == "livekit_token_issuer"
    assert settings.livekit_token_issuer_enabled is True


def test_production_rejects_livekit_token_issuer_mode(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WEBCALL_AI_PARTICIPANT_MODE", "livekit_token_issuer")
    monkeypatch.setenv("WEBCALL_AI_LIVEKIT_TOKEN_ISSUER_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    with pytest.raises(RuntimeError, match="livekit_token_issuer"):
        get_webcall_ai_settings()


def test_production_rejects_participant_enabled(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WEBCALL_AI_PARTICIPANT_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    with pytest.raises(RuntimeError, match="WEBCALL_AI_PARTICIPANT_ENABLED"):
        get_webcall_ai_settings()


def test_room_presence_fake_no_media_allowed_when_enabled(monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_ROOM_PRESENCE_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_ROOM_PRESENCE_MODE", "fake_no_media")
    get_webcall_ai_settings.cache_clear()

    settings = get_webcall_ai_settings()

    assert settings.room_presence_enabled is True
    assert settings.room_presence_mode == "fake_no_media"


def test_room_presence_timeout_is_bounded(monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_ROOM_PRESENCE_JOIN_TIMEOUT_MS", "999999")
    get_webcall_ai_settings.cache_clear()

    assert get_webcall_ai_settings().room_presence_join_timeout_ms == 30000


def test_livekit_room_presence_requires_token_issuer_mode(monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_ROOM_PRESENCE_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_ROOM_PRESENCE_MODE", "livekit_no_media")
    monkeypatch.setenv("WEBCALL_AI_PARTICIPANT_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    with pytest.raises(RuntimeError, match="WEBCALL_AI_PARTICIPANT_MODE"):
        get_webcall_ai_settings()


def test_livekit_room_presence_allowed_with_required_flags(monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_ROOM_PRESENCE_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_ROOM_PRESENCE_MODE", "livekit_no_media")
    monkeypatch.setenv("WEBCALL_AI_PARTICIPANT_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_PARTICIPANT_MODE", "livekit_token_issuer")
    monkeypatch.setenv("WEBCALL_AI_LIVEKIT_TOKEN_ISSUER_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    settings = get_webcall_ai_settings()

    assert settings.room_presence_enabled is True
    assert settings.room_presence_mode == "livekit_no_media"


def test_production_rejects_room_presence_enabled(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WEBCALL_AI_ROOM_PRESENCE_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    with pytest.raises(RuntimeError, match="WEBCALL_AI_ROOM_PRESENCE_ENABLED"):
        get_webcall_ai_settings()


def test_stt_runtime_mock_text_allowed_when_enabled(monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_STT_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_STT_RUNTIME_MODE", "mock_text")
    get_webcall_ai_settings.cache_clear()

    settings = get_webcall_ai_settings()

    assert settings.stt_runtime_enabled is True
    assert settings.stt_runtime_mode == "mock_text"
    assert settings.stt_transcript_write_enabled is False


def test_stt_transcript_write_requires_explicit_flag(monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_STT_RUNTIME_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    assert get_webcall_ai_settings().stt_transcript_write_enabled is False

    monkeypatch.setenv("WEBCALL_AI_STT_TRANSCRIPT_WRITE_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    assert get_webcall_ai_settings().stt_transcript_write_enabled is True


def test_invalid_stt_runtime_mode_fails_closed(monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_STT_RUNTIME_MODE", "live_audio")
    get_webcall_ai_settings.cache_clear()

    with pytest.raises(RuntimeError, match="WEBCALL_AI_STT_RUNTIME_MODE"):
        get_webcall_ai_settings()


def test_production_rejects_stt_runtime_enabled(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WEBCALL_AI_STT_RUNTIME_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    with pytest.raises(RuntimeError, match="WEBCALL_AI_STT_RUNTIME_ENABLED"):
        get_webcall_ai_settings()


def test_orchestrator_deterministic_mode_allowed_when_enabled(monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_ORCHESTRATOR_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    settings = get_webcall_ai_settings()

    assert settings.orchestrator_enabled is True
    assert settings.orchestrator_mode == "deterministic_tracking"
    assert settings.tracking_country_code == "CH"


def test_tracking_lookup_requires_orchestrator_enabled(monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_TRACKING_LOOKUP_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    with pytest.raises(RuntimeError, match="WEBCALL_AI_ORCHESTRATOR_ENABLED"):
        get_webcall_ai_settings()


def test_tracking_lookup_allowed_with_orchestrator_enabled(monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_ORCHESTRATOR_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_TRACKING_LOOKUP_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_TRACKING_REPLY_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_TRACKING_COUNTRY_CODE", "nl")
    monkeypatch.setenv("WEBCALL_AI_TRACKING_LOOKUP_TIMEOUT_MS", "999999")
    get_webcall_ai_settings.cache_clear()

    settings = get_webcall_ai_settings()

    assert settings.tracking_lookup_enabled is True
    assert settings.tracking_reply_enabled is True
    assert settings.tracking_country_code == "NL"
    assert settings.tracking_lookup_timeout_ms == 30000


def test_production_rejects_orchestrator_enabled(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WEBCALL_AI_ORCHESTRATOR_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    with pytest.raises(RuntimeError, match="WEBCALL_AI_ORCHESTRATOR_ENABLED"):
        get_webcall_ai_settings()


def test_tts_runtime_mock_audio_reference_allowed_when_enabled(monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_TTS_RUNTIME_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    settings = get_webcall_ai_settings()

    assert settings.tts_runtime_enabled is True
    assert settings.tts_runtime_mode == "mock_audio_reference"


def test_voice_egress_requires_tts_runtime_enabled(monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_VOICE_EGRESS_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    with pytest.raises(RuntimeError, match="WEBCALL_AI_TTS_RUNTIME_ENABLED"):
        get_webcall_ai_settings()


def test_voice_egress_fake_mode_allowed_with_tts_runtime(monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_TTS_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_VOICE_EGRESS_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_VOICE_EGRESS_MODE", "fake_audio_reference")
    get_webcall_ai_settings.cache_clear()

    settings = get_webcall_ai_settings()

    assert settings.voice_egress_enabled is True
    assert settings.voice_egress_mode == "fake_audio_reference"


def test_tts_runtime_and_voice_egress_invalid_modes_fail_closed(monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_TTS_RUNTIME_MODE", "live_provider")
    get_webcall_ai_settings.cache_clear()

    with pytest.raises(RuntimeError, match="WEBCALL_AI_TTS_RUNTIME_MODE"):
        get_webcall_ai_settings()

    monkeypatch.setenv("WEBCALL_AI_TTS_RUNTIME_MODE", "mock_audio_reference")
    monkeypatch.setenv("WEBCALL_AI_VOICE_EGRESS_MODE", "livekit_publish_track")
    get_webcall_ai_settings.cache_clear()

    with pytest.raises(RuntimeError, match="WEBCALL_AI_VOICE_EGRESS_MODE"):
        get_webcall_ai_settings()


def test_production_rejects_tts_runtime_and_voice_egress(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WEBCALL_AI_TTS_RUNTIME_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    with pytest.raises(RuntimeError, match="WEBCALL_AI_TTS_RUNTIME_ENABLED"):
        get_webcall_ai_settings()

    monkeypatch.setenv("WEBCALL_AI_TTS_RUNTIME_ENABLED", "false")
    monkeypatch.setenv("WEBCALL_AI_VOICE_EGRESS_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    with pytest.raises(RuntimeError, match="WEBCALL_AI_TTS_RUNTIME_ENABLED|WEBCALL_AI_VOICE_EGRESS_ENABLED"):
        get_webcall_ai_settings()


def test_pilot_closure_flags_validate_fail_closed(monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_PILOT_CLOSURE_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_PILOT_KILL_SWITCH", "false")
    get_webcall_ai_settings.cache_clear()

    with pytest.raises(RuntimeError, match="WEBCALL_AI_PILOT_EVIDENCE_ENABLED"):
        get_webcall_ai_settings()

    monkeypatch.setenv("WEBCALL_AI_PILOT_EVIDENCE_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_PILOT_CANARY_PERCENT", "99")
    get_webcall_ai_settings.cache_clear()

    assert get_webcall_ai_settings().pilot_canary_percent == 1


def test_production_rejects_pilot_closure_and_fixture(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WEBCALL_AI_PILOT_CLOSURE_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    with pytest.raises(RuntimeError, match="WEBCALL_AI_PILOT_CLOSURE_ENABLED"):
        get_webcall_ai_settings()

    monkeypatch.setenv("WEBCALL_AI_PILOT_CLOSURE_ENABLED", "false")
    monkeypatch.setenv("WEBCALL_AI_PILOT_FIXTURE_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    with pytest.raises(RuntimeError, match="WEBCALL_AI_PILOT_FIXTURE_ENABLED"):
        get_webcall_ai_settings()


def test_production_pilot_closure_gate_message_is_stable(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WEBCALL_AI_PILOT_CLOSURE_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    with pytest.raises(RuntimeError) as exc_info:
        get_webcall_ai_settings()

    assert str(exc_info.value) == "WEBCALL_AI_PILOT_CLOSURE_ENABLED must be false in production"


def test_no_test_leaks_webcall_ai_environment():
    assert not any(key for key in WEBCALL_ENV_KEYS if key != "APP_ENV" and key in os.environ)
