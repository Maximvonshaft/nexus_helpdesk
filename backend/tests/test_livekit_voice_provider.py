from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/livekit_voice_provider_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.services.livekit_voice_provider import LiveKitVoiceProvider, _server_api_url
from app.webchat_voice_config import load_webchat_voice_runtime_config


class FakeVideoGrants:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class FakeAccessToken:
    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.identity = None
        self.ttl = None
        self.grants = None

    def with_identity(self, identity: str):
        self.identity = identity
        return self

    def with_ttl(self, ttl):
        self.ttl = ttl
        return self

    def with_grants(self, grants: FakeVideoGrants):
        self.grants = grants
        return self

    def to_jwt(self):
        return f"fake.jwt.identity={self.identity}.room={self.grants.kwargs['room']}"


def test_livekit_runtime_config_requires_livekit_env(monkeypatch):
    monkeypatch.setenv("WEBCHAT_VOICE_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_VOICE_PROVIDER", "livekit")
    monkeypatch.delenv("LIVEKIT_URL", raising=False)
    monkeypatch.delenv("LIVEKIT_API_KEY", raising=False)
    monkeypatch.delenv("LIVEKIT_API_SECRET", raising=False)

    with pytest.raises(RuntimeError, match="LIVEKIT_URL must be set"):
        load_webchat_voice_runtime_config()


def test_livekit_runtime_config_requires_wss_connect_src(monkeypatch):
    monkeypatch.setenv("WEBCHAT_VOICE_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_VOICE_PROVIDER", "livekit")
    monkeypatch.setenv("LIVEKIT_URL", "wss://voice.example.test")
    monkeypatch.setenv("LIVEKIT_API_KEY", "unit_key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "unit_secret")
    monkeypatch.setenv("WEBCHAT_VOICE_CONNECT_SRC", "https://voice.example.test")

    with pytest.raises(RuntimeError, match="must include the LiveKit wss URL"):
        load_webchat_voice_runtime_config()


def test_livekit_runtime_config_accepts_required_settings(monkeypatch):
    monkeypatch.setenv("WEBCHAT_VOICE_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_VOICE_PROVIDER", "livekit")
    monkeypatch.setenv("LIVEKIT_URL", "wss://voice.example.test")
    monkeypatch.setenv("LIVEKIT_API_KEY", "unit_key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "unit_secret")
    monkeypatch.setenv("WEBCHAT_VOICE_CONNECT_SRC", "wss://voice.example.test https://voice.example.test")

    config = load_webchat_voice_runtime_config()

    assert config.provider == "livekit"
    assert config.livekit_url == "wss://voice.example.test"
    assert config.livekit_api_key == "unit_key"
    assert config.livekit_api_secret == "unit_secret"


def test_livekit_url_conversion_for_server_api():
    assert _server_api_url("wss://voice.example.test") == "https://voice.example.test"
    assert _server_api_url("ws://localhost:7880") == "http://localhost:7880"
    assert _server_api_url("https://voice.example.test") == "https://voice.example.test"


def test_livekit_provider_issues_room_scoped_token(monkeypatch):
    import app.services.livekit_voice_provider as provider_module

    fake_api = SimpleNamespace(AccessToken=FakeAccessToken, VideoGrants=FakeVideoGrants)
    monkeypatch.setattr(provider_module, "_livekit_api_module", lambda: fake_api)
    provider = LiveKitVoiceProvider(livekit_url="wss://voice.example.test", api_key="unit_key", api_secret="unit_secret")

    token = provider.issue_participant_token(
        room_name="webcall_wv_123",
        participant_identity="visitor_wv_123_initial",
        ttl_seconds=900,
    )

    assert token.provider == "livekit"
    assert token.room_name == "webcall_wv_123"
    assert token.participant_identity == "visitor_wv_123_initial"
    assert "room=webcall_wv_123" in token.participant_token
    assert "unit_secret" not in token.participant_token


def test_livekit_create_room_treats_already_exists_as_success(monkeypatch):
    async def raise_exists(self, *, room_name: str):
        raise RuntimeError("room already exists")

    monkeypatch.setattr(LiveKitVoiceProvider, "_create_room_async", raise_exists)
    provider = LiveKitVoiceProvider(livekit_url="wss://voice.example.test", api_key="unit_key", api_secret="unit_secret")

    assert provider.create_room(room_name="webcall_wv_123") == "webcall_wv_123"


def test_livekit_close_room_does_not_break_nexusdesk_end_flow(monkeypatch):
    async def raise_unavailable(self, *, room_name: str):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(LiveKitVoiceProvider, "_delete_room_async", raise_unavailable)
    provider = LiveKitVoiceProvider(livekit_url="wss://voice.example.test", api_key="unit_key", api_secret="unit_secret")

    assert provider.close_room(room_name="webcall_wv_123") is None


def test_livekit_get_room_status_uses_provider_lookup(monkeypatch):
    async def room_exists(self, *, room_name: str):
        return room_name == "webcall_wv_exists"

    monkeypatch.setattr(LiveKitVoiceProvider, "_room_exists_async", room_exists)
    provider = LiveKitVoiceProvider(livekit_url="wss://voice.example.test", api_key="unit_key", api_secret="unit_secret")

    assert provider.get_room_status(room_name="webcall_wv_exists") == "active"
    assert provider.get_room_status(room_name="webcall_wv_missing") == "not_found"
