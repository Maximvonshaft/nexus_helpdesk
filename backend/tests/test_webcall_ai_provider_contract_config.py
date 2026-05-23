import pytest

from app.services.webcall_ai.config import get_webcall_ai_settings


WEBCALL_PROVIDER_ENV_KEYS = [
    "APP_ENV",
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
]


@pytest.fixture(autouse=True)
def clean_provider_env(monkeypatch):
    get_webcall_ai_settings.cache_clear()
    for key in WEBCALL_PROVIDER_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield
    get_webcall_ai_settings.cache_clear()


def test_provider_contract_defaults_remain_mock_and_fail_closed():
    settings = get_webcall_ai_settings()

    assert settings.stt_provider == "mock"
    assert settings.tts_provider == "mock"
    assert settings.stt_timeout_ms == 5000
    assert settings.tts_timeout_ms == 5000
    assert settings.stt_canary_percent == 0
    assert settings.tts_canary_percent == 0


@pytest.mark.parametrize("key", ["WEBCALL_STT_PROVIDER", "WEBCALL_TTS_PROVIDER"])
def test_disabled_provider_is_accepted(monkeypatch, key):
    monkeypatch.setenv(key, "disabled")
    get_webcall_ai_settings.cache_clear()

    settings = get_webcall_ai_settings()

    assert getattr(settings, "stt_provider" if "STT" in key else "tts_provider") == "disabled"


def test_contract_stub_provider_requires_explicit_enable_flag(monkeypatch):
    monkeypatch.setenv("WEBCALL_STT_PROVIDER", "contract_stub")
    get_webcall_ai_settings.cache_clear()

    with pytest.raises(RuntimeError, match="WEBCALL_STT_CONTRACT_STUB_ENABLED"):
        get_webcall_ai_settings()

    monkeypatch.setenv("WEBCALL_STT_CONTRACT_STUB_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    assert get_webcall_ai_settings().stt_provider == "contract_stub"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("WEBCALL_STT_PROVIDER", "deepgram"),
        ("WEBCALL_STT_PROVIDER", "azure"),
        ("WEBCALL_STT_PROVIDER", "openai_realtime"),
        ("WEBCALL_TTS_PROVIDER", "elevenlabs"),
        ("WEBCALL_TTS_PROVIDER", "google"),
        ("WEBCALL_TTS_PROVIDER", "aws"),
    ],
)
def test_real_provider_names_are_rejected_in_pr5(monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    get_webcall_ai_settings.cache_clear()

    with pytest.raises(RuntimeError, match=key):
        get_webcall_ai_settings()


@pytest.mark.parametrize("key", ["WEBCALL_STT_TOKEN", "WEBCALL_TTS_TOKEN"])
def test_production_rejects_inline_provider_tokens(monkeypatch, key):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv(key, "inline-secret")
    get_webcall_ai_settings.cache_clear()

    with pytest.raises(RuntimeError, match=key):
        get_webcall_ai_settings()


def test_token_file_fields_are_accepted_as_strings(monkeypatch):
    monkeypatch.setenv("WEBCALL_STT_TOKEN_FILE", "/run/secrets/webcall-stt")
    monkeypatch.setenv("WEBCALL_TTS_TOKEN_FILE", "/run/secrets/webcall-tts")
    get_webcall_ai_settings.cache_clear()

    settings = get_webcall_ai_settings()

    assert settings.stt_token_file == "/run/secrets/webcall-stt"
    assert settings.tts_token_file == "/run/secrets/webcall-tts"


def test_canary_percent_and_timeouts_are_bounded(monkeypatch):
    monkeypatch.setenv("WEBCALL_STT_CANARY_PERCENT", "999")
    monkeypatch.setenv("WEBCALL_TTS_CANARY_PERCENT", "-3")
    monkeypatch.setenv("WEBCALL_STT_TIMEOUT_MS", "1")
    monkeypatch.setenv("WEBCALL_TTS_TIMEOUT_MS", "999999")
    get_webcall_ai_settings.cache_clear()

    settings = get_webcall_ai_settings()

    assert settings.stt_canary_percent == 100
    assert settings.tts_canary_percent == 0
    assert settings.stt_timeout_ms == 100
    assert settings.tts_timeout_ms == 30000
