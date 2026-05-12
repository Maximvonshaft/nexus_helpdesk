from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_voice_static_headers_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

VOICE_ENV_KEYS = [
    "WEBCHAT_VOICE_ENABLED",
    "WEBCHAT_VOICE_ALLOWED_PATH_PREFIXES",
    "WEBCHAT_VOICE_CONNECT_SRC",
    "WEBCHAT_VOICE_PROVIDER",
    "WEBCHAT_VOICE_RECORDING_ENABLED",
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


def test_voice_disabled_keeps_microphone_denied_on_voice_path(monkeypatch):
    client = _client(monkeypatch)

    response = client.get("/webchat/voice/session_demo")

    assert response.status_code == 404
    assert _permissions(response) == "camera=(), microphone=(), geolocation=()"
    assert "connect-src 'self'" in _csp(response)
    assert "wss://voice.example.test" not in _csp(response)


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
    assert "connect-src 'self' wss://voice.example.test https://voice.example.test" in policy
    assert "camera" not in policy
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


def test_webchat_demo_and_fast_reply_frontline_path_keep_security_headers(monkeypatch):
    client = _client(
        monkeypatch,
        WEBCHAT_VOICE_ENABLED="true",
        WEBCHAT_VOICE_CONNECT_SRC="wss://voice.example.test",
    )

    demo_response = client.get("/webchat/demo.html")
    api_response = client.post("/api/webchat/fast-reply", json={})

    assert demo_response.status_code in {200, 404}
    assert _permissions(demo_response) == "camera=(), microphone=(), geolocation=()"
    # Empty payload now hits the AI frontline endpoint schema instead of an
    # intake-only blanket block. Formal resolution outbound remains blocked in
    # the Ticket workflow, not at this frontline endpoint.
    assert api_response.status_code == 422
    assert _permissions(api_response) == "camera=(), microphone=(), geolocation=()"
    assert "wss://voice.example.test" not in _csp(api_response)


def test_voice_connect_src_rejects_wildcard(monkeypatch):
    for key in VOICE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("WEBCHAT_VOICE_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_VOICE_CONNECT_SRC", "wss://voice.example.test *")

    import pytest
    from app.webchat_voice_config import load_webchat_voice_runtime_config

    with pytest.raises(RuntimeError, match="must not contain wildcard"):
        load_webchat_voice_runtime_config()
