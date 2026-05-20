from __future__ import annotations

from app.services.provider_runtime_status import get_provider_runtime_status
from app.services.webchat_fast_config import get_webchat_fast_settings


_ENV_KEYS = [
    "APP_ENV",
    "WEBCHAT_FAST_AI_ENABLED",
    "WEBCHAT_FAST_AI_PROVIDER",
    "WEBCHAT_FAST_AI_FALLBACK_PROVIDER",
    "WEBCHAT_FAST_AI_CODEX_ENABLED",
    "WEBCHAT_FAST_AI_CODEX_APP_SERVER_ENABLED",
    "WEBCHAT_FAST_AI_OPENAI_ENABLED",
    "OPENCLAW_RESPONSES_URL",
    "OPENCLAW_RESPONSES_TOKEN",
    "OPENCLAW_RESPONSES_TOKEN_FILE",
    "CODEX_AUTH_TOKEN",
    "CODEX_AUTH_TOKEN_FILE",
    "CODEX_APP_SERVER_BRIDGE_URL",
    "CODEX_APP_SERVER_TOKEN",
    "CODEX_APP_SERVER_TOKEN_FILE",
    "CODEX_APP_SERVER_CANARY_PERCENT",
    "CODEX_APP_SERVER_KILL_SWITCH",
    "OPENAI_API_KEY",
    "OPENAI_API_KEY_FILE",
]


def _clear_env(monkeypatch) -> None:
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    get_webchat_fast_settings.cache_clear()


def test_provider_runtime_status_default_openclaw_not_configured(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "development")
    get_webchat_fast_settings.cache_clear()

    status = get_provider_runtime_status()

    assert status["ok"] is False
    assert status["status"] == "warning"
    assert status["configured_provider"] == "openclaw_responses"
    assert "selected provider openclaw_responses is not configured" in status["warnings"]
    openclaw = next(item for item in status["providers"] if item["name"] == "openclaw_responses")
    assert openclaw["selected"] is True
    assert openclaw["configured"] is False
    assert openclaw["capabilities"]["webchat_fast_reply"] is False
    assert status["boundary"] == {
        "secret_values_exposed": False,
        "external_network_call": False,
        "customer_message_sent": False,
    }


def test_provider_runtime_status_codex_app_server_ready_without_secret_echo(monkeypatch):
    _clear_env(monkeypatch)
    secret = "local-codex-secret-value"
    openclaw_secret = "local-openclaw-secret-value"
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("WEBCHAT_FAST_AI_PROVIDER", "codex_app_server")
    monkeypatch.setenv("WEBCHAT_FAST_AI_FALLBACK_PROVIDER", "openclaw_responses")
    monkeypatch.setenv("WEBCHAT_FAST_AI_CODEX_APP_SERVER_ENABLED", "true")
    monkeypatch.setenv("CODEX_APP_SERVER_BRIDGE_URL", "http://127.0.0.1:18793/reply")
    monkeypatch.setenv("CODEX_APP_SERVER_TOKEN", secret)
    monkeypatch.setenv("CODEX_APP_SERVER_CANARY_PERCENT", "100")
    monkeypatch.setenv("OPENCLAW_RESPONSES_URL", "http://127.0.0.1:18791/reply")
    monkeypatch.setenv("OPENCLAW_RESPONSES_TOKEN", openclaw_secret)
    get_webchat_fast_settings.cache_clear()

    status = get_provider_runtime_status()

    assert status["ok"] is True
    assert status["status"] == "ready"
    assert status["configured_provider"] == "codex_app_server"
    assert status["fallback_provider"] == "openclaw_responses"
    codex = next(item for item in status["providers"] if item["name"] == "codex_app_server")
    assert codex["selected"] is True
    assert codex["feature_enabled"] is True
    assert codex["configured"] is True
    assert codex["runtime"] == "private_sidecar_provider"
    assert codex["safety_level"] == "reply_only"
    assert codex["capabilities"]["webchat_fast_reply"] is True
    assert codex["capabilities"]["tool_execution"] is False
    assert codex["capabilities"]["ticket_action"] is False
    assert codex["controls"] == {
        "canary_percent": 100,
        "kill_switch": False,
        "fallback_provider": "openclaw_responses",
    }
    assert codex["diagnostics"]["bridge_url_configured"] is True
    assert codex["diagnostics"]["token_configured"] is True
    rendered = str(status)
    assert secret not in rendered
    assert openclaw_secret not in rendered


def test_provider_runtime_status_codex_canary_requires_openclaw_fallback(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("WEBCHAT_FAST_AI_PROVIDER", "codex_app_server")
    monkeypatch.setenv("WEBCHAT_FAST_AI_FALLBACK_PROVIDER", "none")
    monkeypatch.setenv("WEBCHAT_FAST_AI_CODEX_APP_SERVER_ENABLED", "true")
    monkeypatch.setenv("CODEX_APP_SERVER_BRIDGE_URL", "http://127.0.0.1:18793/reply")
    monkeypatch.setenv("CODEX_APP_SERVER_TOKEN", "local-codex-secret-value")
    monkeypatch.setenv("CODEX_APP_SERVER_CANARY_PERCENT", "10")
    get_webchat_fast_settings.cache_clear()

    status = get_provider_runtime_status()

    assert status["ok"] is False
    assert "codex_app_server canary below 100 requires openclaw_responses fallback for skipped traffic" in status["warnings"]


def test_provider_runtime_status_reports_misconfiguration_without_throwing(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("WEBCHAT_FAST_AI_PROVIDER", "codex_app_server")
    monkeypatch.delenv("WEBCHAT_FAST_AI_CODEX_APP_SERVER_ENABLED", raising=False)
    get_webchat_fast_settings.cache_clear()

    status = get_provider_runtime_status()

    assert status["ok"] is False
    assert status["status"] == "misconfigured"
    assert "WEBCHAT_FAST_AI_CODEX_APP_SERVER_ENABLED" in status["config_error"]
    assert status["providers"] == []
    assert status["boundary"]["secret_values_exposed"] is False
