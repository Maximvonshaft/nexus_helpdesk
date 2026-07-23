from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/webchat_voice_static_headers_tests.db",
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

LIVEKIT_URL_ENV = "LIVEKIT_URL"
LIVEKIT_KEY_ENV = "LIVEKIT_API_KEY"
LIVEKIT_KEY_FILE_ENV = "LIVEKIT_API_KEY_FILE"
LIVEKIT_SECRET_ENV = "LIVEKIT_API_SECRET"
LIVEKIT_SECRET_FILE_ENV = "LIVEKIT_API_SECRET_FILE"

VOICE_ENV_KEYS = [
    "WEBCHAT_HUMAN_CALL_ENABLED",
    "WEBCHAT_LIVE_AI_VOICE_ENABLED",
    "WEBCHAT_VOICE_ALLOWED_PATH_PREFIXES",
    "WEBCHAT_VOICE_CONNECT_SRC",
    "WEBCHAT_VOICE_PROVIDER",
    "WEBCHAT_VOICE_RECORDING_ENABLED",
    LIVEKIT_URL_ENV,
    LIVEKIT_KEY_ENV,
    LIVEKIT_KEY_FILE_ENV,
    LIVEKIT_SECRET_ENV,
    LIVEKIT_SECRET_FILE_ENV,
]


def _client(monkeypatch, **env: str) -> TestClient:
    for key in VOICE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("WEBCHAT_VOICE_ALLOWED_PATH_PREFIXES", "/webcall")
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


def test_retired_voice_route_is_absent_and_microphone_denied(monkeypatch):
    response = _client(monkeypatch).get("/webchat/voice/session_demo")

    assert response.status_code == 404
    assert _permissions(response) == "camera=(), microphone=(), geolocation=()"
    assert "connect-src 'self'" in _csp(response)


def test_retired_pcm_health_route_is_always_absent(monkeypatch):
    client = _client(
        monkeypatch,
        WEBCHAT_HUMAN_CALL_ENABLED="false",
        WEBCHAT_LIVE_AI_VOICE_ENABLED="true",
        WEBCHAT_VOICE_PROVIDER="mock",
    )

    response = client.get("/webchat/live/health")
    runtime_config = client.get("/api/webchat/voice/runtime-config")

    assert response.status_code == 404
    assert _permissions(response) == "camera=(), microphone=(), geolocation=()"
    assert runtime_config.status_code == 200
    assert runtime_config.json()["media_plane"] == "mock"
    assert "upstream" not in runtime_config.text.lower()


def test_non_voice_route_never_receives_microphone_permission(monkeypatch):
    client = _client(
        monkeypatch,
        WEBCHAT_HUMAN_CALL_ENABLED="true",
        WEBCHAT_VOICE_PROVIDER="mock",
        WEBCHAT_VOICE_CONNECT_SRC="wss://voice.example.test",
    )

    response = client.get("/healthz")

    assert response.status_code == 200
    assert _permissions(response) == "camera=(), microphone=(), geolocation=()"
    assert "wss://voice.example.test" not in _csp(response)


def test_disabled_or_mock_voice_does_not_read_livekit_secret_files(
    monkeypatch,
    tmp_path,
):
    disabled = _client(
        monkeypatch,
        WEBCHAT_VOICE_PROVIDER="mock",
        **{
            LIVEKIT_KEY_FILE_ENV: str(tmp_path),
            LIVEKIT_SECRET_FILE_ENV: str(tmp_path),
        },
    )
    assert disabled.get("/healthz").status_code == 200

    mock = _client(
        monkeypatch,
        WEBCHAT_HUMAN_CALL_ENABLED="true",
        WEBCHAT_VOICE_PROVIDER="mock",
        WEBCHAT_VOICE_CONNECT_SRC="wss://voice.example.test",
        **{
            LIVEKIT_KEY_FILE_ENV: str(tmp_path),
            LIVEKIT_SECRET_FILE_ENV: str(tmp_path),
        },
    )
    assert mock.get("/healthz").status_code == 200


def test_enabled_livekit_fails_closed_when_secret_file_is_unreadable(
    monkeypatch,
    tmp_path,
):
    client = _client(
        monkeypatch,
        WEBCHAT_HUMAN_CALL_ENABLED="true",
        WEBCHAT_VOICE_PROVIDER="livekit",
        WEBCHAT_VOICE_CONNECT_SRC="wss://voice.example.test",
        **{
            LIVEKIT_URL_ENV: "wss://voice.example.test",
            LIVEKIT_KEY_ENV: "unit_key",
            LIVEKIT_SECRET_FILE_ENV: str(tmp_path),
        },
    )

    assert client.get("/healthz").status_code == 200
    from app.webchat_voice_config import load_webchat_voice_runtime_config

    with pytest.raises(RuntimeError, match="cannot be read"):
        load_webchat_voice_runtime_config()


def test_only_webcall_path_receives_microphone_and_livekit_sources(monkeypatch):
    client = _client(
        monkeypatch,
        WEBCHAT_HUMAN_CALL_ENABLED="true",
        WEBCHAT_VOICE_CONNECT_SRC=(
            "wss://voice.example.test https://voice.example.test"
        ),
    )

    retired = client.get("/webchat/voice/session_demo")
    assert retired.status_code == 404
    assert _permissions(retired) == "camera=(), microphone=(), geolocation=()"
    assert "wss://voice.example.test" not in _csp(retired)

    webcall = client.get("/webcall/session_demo")
    assert webcall.status_code in {200, 404}
    assert _permissions(webcall) == "camera=(), microphone=(self), geolocation=()"
    policy = _csp(webcall)
    _assert_connect_sources(
        policy,
        "'self'",
        "wss://voice.example.test",
        "https://voice.example.test",
    )
    assert "unsafe-eval" not in policy


def test_noncanonical_voice_prefix_is_rejected(monkeypatch):
    for key in VOICE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("WEBCHAT_HUMAN_CALL_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_VOICE_ALLOWED_PATH_PREFIXES", "/voice/webchat")

    from app.webchat_voice_config import load_webchat_voice_runtime_config

    with pytest.raises(
        RuntimeError,
        match="may only grant microphone access to /webcall",
    ):
        load_webchat_voice_runtime_config()


def test_voice_connect_src_rejects_wildcards(monkeypatch):
    for key in VOICE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("WEBCHAT_HUMAN_CALL_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_VOICE_ALLOWED_PATH_PREFIXES", "/webcall")
    monkeypatch.setenv(
        "WEBCHAT_VOICE_CONNECT_SRC",
        "wss://voice.example.test *",
    )

    from app.webchat_voice_config import load_webchat_voice_runtime_config

    with pytest.raises(RuntimeError, match="must not contain wildcard"):
        load_webchat_voice_runtime_config()
