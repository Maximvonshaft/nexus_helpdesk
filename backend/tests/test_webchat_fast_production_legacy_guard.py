from __future__ import annotations

import pytest

from app.services.webchat_fast_config import get_webchat_fast_settings


_FAST_REPLY_ENV = (
    "APP_ENV",
    "WEBCHAT_FAST_AI_ENABLED",
    "WEBCHAT_FAST_AI_PROVIDER",
    "WEBCHAT_FAST_AI_FALLBACK_PROVIDER",
    "WEBCHAT_FAST_AI_CODEX_ENABLED",
    "WEBCHAT_FAST_AI_CODEX_APP_SERVER_ENABLED",
    "WEBCHAT_FAST_AI_OPENAI_ENABLED",
    "CODEX_AUTH_TOKEN",
    "CODEX_AUTH_TOKEN_FILE",
    "CODEX_APP_SERVER_BRIDGE_URL",
    "CODEX_APP_SERVER_TOKEN",
    "CODEX_APP_SERVER_TOKEN_FILE",
    "CODEX_REPLY_BRIDGE_TOKEN",
    "CODEX_REPLY_BRIDGE_TOKEN_FILE",
    "OPENAI_API_KEY",
    "OPENAI_API_KEY_FILE",
    "OPENCLAW_RESPONSES_URL",
    "OPENCLAW_RESPONSES_TOKEN",
    "OPENCLAW_RESPONSES_TOKEN_FILE",
    "WEBCHAT_FAST_STREAM_ENABLED",
    "OPENCLAW_RESPONSES_STREAM_URL",
    "OPENCLAW_RESPONSES_STREAM_TOKEN",
    "OPENCLAW_RESPONSES_STREAM_TOKEN_FILE",
)


@pytest.fixture(autouse=True)
def _settings_cache_isolation():
    get_webchat_fast_settings.cache_clear()
    yield
    get_webchat_fast_settings.cache_clear()


def _clear_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _FAST_REPLY_ENV:
        monkeypatch.delenv(name, raising=False)
    get_webchat_fast_settings.cache_clear()


def test_production_provider_runtime_is_allowed(monkeypatch):
    _clear_settings(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WEBCHAT_FAST_AI_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_FAST_AI_PROVIDER", "provider_runtime")

    settings = get_webchat_fast_settings()

    assert settings.enabled is True
    assert settings.provider == "provider_runtime"


@pytest.mark.parametrize(
    "provider",
    ["codex_auth", "codex_app_server", "openclaw_responses", "openai_responses"],
)
def test_production_forbids_legacy_direct_fast_reply_providers(monkeypatch, provider):
    _clear_settings(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WEBCHAT_FAST_AI_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_FAST_AI_PROVIDER", provider)

    with pytest.raises(RuntimeError) as exc:
        get_webchat_fast_settings()

    message = str(exc.value)
    assert "WEBCHAT_FAST_AI_PROVIDER=provider_runtime" in message
    assert provider in message


@pytest.mark.parametrize("app_env", ["development", "test"])
@pytest.mark.parametrize(
    "provider",
    ["codex_auth", "codex_app_server", "openclaw_responses", "openai_responses"],
)
def test_non_production_keeps_legacy_provider_compatibility(monkeypatch, app_env, provider):
    _clear_settings(monkeypatch)
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("WEBCHAT_FAST_AI_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_FAST_AI_PROVIDER", provider)
    if provider == "codex_auth":
        monkeypatch.setenv("WEBCHAT_FAST_AI_CODEX_ENABLED", "true")
    if provider == "codex_app_server":
        monkeypatch.setenv("WEBCHAT_FAST_AI_CODEX_APP_SERVER_ENABLED", "true")
        monkeypatch.setenv("CODEX_APP_SERVER_BRIDGE_URL", "http://127.0.0.1:18794/reply")
        monkeypatch.setenv("CODEX_APP_SERVER_TOKEN", "local-token")
    if provider == "openai_responses":
        monkeypatch.setenv("WEBCHAT_FAST_AI_OPENAI_ENABLED", "true")

    settings = get_webchat_fast_settings()

    assert settings.app_env == app_env
    assert settings.provider == provider
