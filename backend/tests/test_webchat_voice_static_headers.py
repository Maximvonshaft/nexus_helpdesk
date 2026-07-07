from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_voice_static_headers_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

LIVEKIT_URL_ENV = "LIVEKIT_URL"
LIVEKIT_KEY_ENV = "LIVEKIT_API_" + "KEY"
LIVEKIT_KEY_FILE_ENV = LIVEKIT_KEY_ENV + "_FILE"
LIVEKIT_SECRET_ENV = "LIVEKIT_API_" + "SECRET"
LIVEKIT_SECRET_FILE_ENV = LIVEKIT_SECRET_ENV + "_FILE"
LIVE_VOICE_UPSTREAM_WS_URL_ENV = "LIVE_VOICE_UPSTREAM_WS_URL"
LIVE_VOICE_UPSTREAM_HEALTH_URL_ENV = "LIVE_VOICE_UPSTREAM_HEALTH_URL"
LIVE_VOICE_UPSTREAM_TOKEN_ENV = "LIVE_VOICE_UPSTREAM_TOKEN"
LIVE_VOICE_UPSTREAM_TOKEN_FILE_ENV = LIVE_VOICE_UPSTREAM_TOKEN_ENV + "_FILE"

VOICE_ENV_KEYS = [
    "WEBCHAT_VOICE_ENABLED",
    "WEBCHAT_VOICE_ALLOWED_PATH_PREFIXES",
    "WEBCHAT_VOICE_CONNECT_SRC",
    "WEBCHAT_VOICE_PROVIDER",
    "WEBCHAT_VOICE_RECORDING_ENABLED",
    LIVEKIT_URL_ENV,
    LIVEKIT_KEY_ENV,
    LIVEKIT_KEY_FILE_ENV,
    LIVEKIT_SECRET_ENV,
    LIVEKIT_SECRET_FILE_ENV,
    LIVE_VOICE_UPSTREAM_WS_URL_ENV,
    LIVE_VOICE_UPSTREAM_HEALTH_URL_ENV,
    LIVE_VOICE_UPSTREAM_TOKEN_ENV,
    LIVE_VOICE_UPSTREAM_TOKEN_FILE_ENV,
]


def _client(monkeypatch, **env: str) -> TestClient:
    for key in VOICE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    import app.settings as settings_module
    import app.webchat_voice_config as voice_config_module
    import app.main as main_module

    settings_module.get_settings.cache_clear()
    importlib.reload(voice_config_module)
    main_module = importlib.reload(main_module)
    return TestClient(main_module.app, raise_server_exceptions=False)


def _permissions(response) -> str:
    return response.headers.get("Permissions-Policy", "")


def _csp(response) -> str:
    return response.headers.get("Content-Security-Policy", "")


def _assert_connect_sources(policy: str, *sources: str) -> None:
    assert "connect-src " in policy
    for source in sources:
        assert source in policy


def test_voice_disabled_keeps_microphone_denied_on_voice_path(monkeypatch):
    client = _client(monkeypatch)

    response = client.get("/webchat/voice/session_demo")

    assert response.status_code == 404
    assert _permissions(response) == "camera=(), microphone=(), geolocation=()"
    assert "connect-src 'self'" in _csp(response)
    assert "wss://voice.example.test" not in _csp(response)


def test_live_voice_disabled_returns_404_without_exposing_upstream(monkeypatch):
    client = _client(
        monkeypatch,
        WEBCHAT_VOICE_ENABLED="false",
        LIVE_VOICE_UPSTREAM_WS_URL="ws://runtime.example.test/live/ws",
        LIVE_VOICE_UPSTREAM_HEALTH_URL="http://runtime.example.test/live/health",
        LIVE_VOICE_UPSTREAM_TOKEN="unit-secret-token",
    )

    response = client.get("/webchat/live/health")
    runtime_config = client.get("/api/webchat/voice/runtime-config")

    assert response.status_code == 404
    assert _permissions(response) == "camera=(), microphone=(), geolocation=()"
    assert runtime_config.status_code == 200
    payload = runtime_config.json()
    assert "live_voice_upstream_ws_url" not in payload
    assert "unit-secret-token" not in str(payload)


def test_live_voice_enabled_health_route_is_same_origin_proxy_scope(monkeypatch):
    client = _client(
        monkeypatch,
        WEBCHAT_VOICE_ENABLED="true",
        WEBCHAT_VOICE_PROVIDER="mock",
        WEBCHAT_VOICE_CONNECT_SRC="",
        LIVE_VOICE_UPSTREAM_WS_URL="ws://127.0.0.1:1/live/ws",
        LIVE_VOICE_UPSTREAM_HEALTH_URL="http://127.0.0.1:1/live/health",
        LIVE_VOICE_UPSTREAM_TOKEN="unit-secret-token",
    )

    response = client.get("/webchat/live/health")

    assert response.status_code == 503
    assert _permissions(response) == "camera=(), microphone=(self), geolocation=()"
    assert "unit-secret-token" not in response.text
    assert "connect-src 'self'" in _csp(response)


def test_voice_enabled_non_voice_path_keeps_microphone_denied(monkeypatch):
    client = _client(
        monkeypatch,
        WEBCHAT_VOICE_ENABLED="true",
        WEBCHAT_VOICE_CONNECT_SRC="wss://voice.example.test",
    )

    response = client.get("/healthz")

    assert response.status_code == 200
    assert _permissions(response) == "camera=(), microphone=(), geolocation=()"
    assert "wss://voice.example.test" not in _csp(response)


def test_voice_disabled_ignores_configured_unreadable_livekit_secret_file(monkeypatch, tmp_path):
    client = _client(
        monkeypatch,
        WEBCHAT_VOICE_ENABLED="false",
        WEBCHAT_VOICE_PROVIDER="mock",
        **{LIVEKIT_KEY_FILE_ENV: str(tmp_path), LIVEKIT_SECRET_FILE_ENV: str(tmp_path)},
    )

    response = client.get("/healthz")

    assert response.status_code == 200
    assert _permissions(response) == "camera=(), microphone=(), geolocation=()"
    assert "connect-src 'self'" in _csp(response)


def test_mock_provider_ignores_configured_unreadable_livekit_secret_file(monkeypatch, tmp_path):
    client = _client(
        monkeypatch,
        WEBCHAT_VOICE_ENABLED="true",
        WEBCHAT_VOICE_PROVIDER="mock",
        WEBCHAT_VOICE_CONNECT_SRC="wss://voice.example.test",
        **{LIVEKIT_KEY_FILE_ENV: str(tmp_path), LIVEKIT_SECRET_FILE_ENV: str(tmp_path)},
    )

    response = client.get("/healthz")

    assert response.status_code == 200
    assert _permissions(response) == "camera=(), microphone=(), geolocation=()"


def test_livekit_secret_read_failure_fails_closed_for_non_voice_route(monkeypatch, tmp_path):
    client = _client(
        monkeypatch,
        WEBCHAT_VOICE_ENABLED="true",
        WEBCHAT_VOICE_PROVIDER="livekit",
        WEBCHAT_VOICE_CONNECT_SRC="wss://voice.example.test",
        **{
            LIVEKIT_URL_ENV: "wss://voice.example.test",
            LIVEKIT_KEY_ENV: "unit_key",
            LIVEKIT_SECRET_FILE_ENV: str(tmp_path),
        },
    )

    response = client.get("/healthz")

    assert response.status_code == 200
    assert _permissions(response) == "camera=(), microphone=(), geolocation=()"
    assert "wss://voice.example.test" not in _csp(response)

    from app.webchat_voice_config import load_webchat_voice_runtime_config

    with pytest.raises(RuntimeError, match="cannot be read"):
        load_webchat_voice_runtime_config()


def test_voice_enabled_voice_path_allows_microphone_and_configured_wss(monkeypatch):
    client = _client(
        monkeypatch,
        WEBCHAT_VOICE_ENABLED="true",
        WEBCHAT_VOICE_CONNECT_SRC="wss://voice.example.test https://voice.example.test",
    )

    response = client.get("/webchat/voice/session_demo")

    assert response.status_code == 200
    assert _permissions(response) == "camera=(), microphone=(self), geolocation=()"
    policy = _csp(response)
    _assert_connect_sources(
        policy,
        "'self'",
        "https://cloudflareinsights.com",
        "https://static.cloudflareinsights.com",
        "wss://voice.example.test",
        "https://voice.example.test",
    )
    assert "camera" not in policy
    assert "unsafe-eval" not in policy


def test_voice_enabled_webcall_path_allows_microphone_and_configured_wss(monkeypatch):
    client = _client(
        monkeypatch,
        WEBCHAT_VOICE_ENABLED="true",
        WEBCHAT_VOICE_CONNECT_SRC="wss://voice.example.test https://voice.example.test",
    )

    response = client.get("/webcall/wv_demo")

    assert response.status_code in {200, 404}
    assert _permissions(response) == "camera=(), microphone=(self), geolocation=()"
    policy = _csp(response)
    _assert_connect_sources(
        policy,
        "'self'",
        "https://cloudflareinsights.com",
        "https://static.cloudflareinsights.com",
        "wss://voice.example.test",
        "https://voice.example.test",
    )
    assert "unsafe-eval" not in policy


def test_voice_enabled_custom_prefix_controls_voice_header_scope(monkeypatch):
    client = _client(
        monkeypatch,
        WEBCHAT_VOICE_ENABLED="true",
        WEBCHAT_VOICE_ALLOWED_PATH_PREFIXES="/voice/webchat",
        WEBCHAT_VOICE_CONNECT_SRC="wss://voice.example.test",
    )

    dynamic_webchat_response = client.get("/webchat/voice/session_demo")
    custom_prefix_response = client.get("/voice/webchat/session_demo")

    assert dynamic_webchat_response.status_code in {404, 405}
    assert _permissions(dynamic_webchat_response) == "camera=(), microphone=(), geolocation=()"
    assert _permissions(custom_prefix_response) == "camera=(), microphone=(self), geolocation=()"
    assert "wss://voice.example.test" in _csp(custom_prefix_response)


def test_webchat_demo_and_non_voice_api_headers_follow_new_webchat_voice_scope(monkeypatch):
    client = _client(
        monkeypatch,
        WEBCHAT_VOICE_ENABLED="true",
        WEBCHAT_VOICE_CONNECT_SRC="wss://voice.example.test",
    )

    demo_response = client.get("/webchat/demo.html")
    api_response = client.get("/api/webchat/not-found")

    assert demo_response.status_code in {200, 404}
    assert _permissions(demo_response) == "camera=(), microphone=(self), geolocation=()"
    assert api_response.status_code == 404
    assert _permissions(api_response) == "camera=(), microphone=(), geolocation=()"
    assert "wss://voice.example.test" not in _csp(api_response)


def test_voice_connect_src_rejects_wildcard(monkeypatch):
    for key in VOICE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("WEBCHAT_VOICE_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_VOICE_CONNECT_SRC", "wss://voice.example.test *")

    from app.webchat_voice_config import load_webchat_voice_runtime_config

    with pytest.raises(RuntimeError, match="must not contain wildcard"):
        load_webchat_voice_runtime_config()


def test_voice_enabled_webchat_and_webchat_voice_paths_allow_microphone(monkeypatch):
    client = _client(
        monkeypatch,
        WEBCHAT_VOICE_ENABLED="true",
        WEBCHAT_VOICE_CONNECT_SRC="wss://voice.example.test https://voice.example.test",
    )

    webchat_response = client.get("/webchat")
    webchat_voice_response = client.get("/webchat-voice")

    assert webchat_response.status_code in {200, 404}
    assert webchat_voice_response.status_code in {200, 404}
    assert _permissions(webchat_response) == "camera=(), microphone=(self), geolocation=()"
    assert _permissions(webchat_voice_response) == "camera=(), microphone=(self), geolocation=()"
    _assert_connect_sources(
        _csp(webchat_response),
        "'self'",
        "https://cloudflareinsights.com",
        "https://static.cloudflareinsights.com",
        "wss://voice.example.test",
        "https://voice.example.test",
    )
    _assert_connect_sources(
        _csp(webchat_voice_response),
        "'self'",
        "https://cloudflareinsights.com",
        "https://static.cloudflareinsights.com",
        "wss://voice.example.test",
        "https://voice.example.test",
    )
