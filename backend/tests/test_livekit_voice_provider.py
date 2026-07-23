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
from app.services.voice_provider import VoiceProviderError
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


def _enable_livekit(monkeypatch) -> None:
    monkeypatch.setenv("WEBCHAT_HUMAN_CALL_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_VOICE_PROVIDER", "livekit")
    monkeypatch.setenv("WEBCHAT_VOICE_ALLOWED_PATH_PREFIXES", "/webcall")


def _provider() -> LiveKitVoiceProvider:
    return LiveKitVoiceProvider(
        livekit_url="wss://voice.example.test",
        api_key="unit_key",
        api_secret="unit_secret",
    )


def test_livekit_runtime_config_requires_livekit_env(monkeypatch):
    _enable_livekit(monkeypatch)
    monkeypatch.delenv("LIVEKIT_URL", raising=False)
    monkeypatch.delenv("LIVEKIT_API_KEY", raising=False)
    monkeypatch.delenv("LIVEKIT_API_SECRET", raising=False)

    with pytest.raises(RuntimeError, match="LIVEKIT_URL must be set"):
        load_webchat_voice_runtime_config()


def test_livekit_runtime_config_requires_wss_connect_src(monkeypatch):
    _enable_livekit(monkeypatch)
    monkeypatch.setenv("LIVEKIT_URL", "wss://voice.example.test")
    monkeypatch.setenv("LIVEKIT_API_KEY", "unit_key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "unit_secret")
    monkeypatch.setenv("WEBCHAT_VOICE_CONNECT_SRC", "https://voice.example.test")

    with pytest.raises(RuntimeError, match="must include the LiveKit wss URL"):
        load_webchat_voice_runtime_config()


def test_livekit_runtime_config_accepts_required_settings(monkeypatch):
    _enable_livekit(monkeypatch)
    monkeypatch.setenv("LIVEKIT_URL", "wss://voice.example.test")
    monkeypatch.setenv("LIVEKIT_API_KEY", "unit_key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "unit_secret")
    monkeypatch.setenv(
        "WEBCHAT_VOICE_CONNECT_SRC",
        "wss://voice.example.test https://voice.example.test",
    )

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
    provider = _provider()

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

    assert _provider().create_room(room_name="webcall_wv_123") == "webcall_wv_123"


def test_livekit_close_room_reports_provider_failure_to_orchestrator(monkeypatch):
    async def raise_unavailable(self, *, room_name: str):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(LiveKitVoiceProvider, "_delete_room_async", raise_unavailable)

    with pytest.raises(VoiceProviderError, match="livekit room close failed"):
        _provider().close_room(room_name="webcall_wv_123")


def test_livekit_get_room_status_uses_provider_lookup(monkeypatch):
    async def room_exists(self, *, room_name: str):
        return room_name == "webcall_wv_exists"

    monkeypatch.setattr(LiveKitVoiceProvider, "_room_exists_async", room_exists)
    provider = _provider()

    assert provider.get_room_status(room_name="webcall_wv_exists") == "active"
    assert provider.get_room_status(room_name="webcall_wv_missing") == "not_found"


def test_warm_consultation_commands_use_one_controller_protocol(monkeypatch):
    commands: list[dict] = []

    async def capture_command(
        self,
        *,
        room_name: str,
        command: dict,
        destination_identity: str,
    ) -> None:
        commands.append(
            {
                "room_name": room_name,
                "command": command,
                "destination_identity": destination_identity,
            }
        )

    monkeypatch.setattr(
        LiveKitVoiceProvider,
        "_send_command_async",
        capture_command,
    )
    provider = _provider()
    common = {
        "room_name": "webcall_wv_123",
        "participant_identity": "caller_1",
        "human_identity": "agent_1",
        "controller_identity": "controller_1",
    }

    started = provider.execute_action(
        action_type="warm_transfer",
        target="+38267000111",
        outbound_trunk_id="trunk_1",
        idempotency_key="consult-start",
        **common,
    )
    completed = provider.execute_action(
        action_type="warm_transfer_complete",
        idempotency_key="consult-complete",
        **common,
    )
    cancelled = provider.execute_action(
        action_type="warm_transfer_cancel",
        idempotency_key="consult-cancel",
        **common,
    )

    assert [started.status, completed.status, cancelled.status] == [
        "awaiting_event",
        "awaiting_event",
        "awaiting_event",
    ]
    assert [entry["command"]["action"] for entry in commands] == [
        "warm_transfer",
        "warm_transfer_complete",
        "warm_transfer_cancel",
    ]
    assert all(
        entry["command"]["participant_identity"] == "caller_1"
        and entry["command"]["human_identity"] == "agent_1"
        and entry["destination_identity"] == "controller_1"
        for entry in commands
    )
    assert commands[0]["command"]["target"] == "+38267000111"
    assert commands[0]["command"]["outbound_trunk_id"] == "trunk_1"
    assert commands[1]["command"]["target"] is None
    assert commands[1]["command"]["outbound_trunk_id"] is None
    assert commands[2]["command"]["target"] is None
    assert commands[2]["command"]["outbound_trunk_id"] is None


def test_recording_start_safe_result_never_exposes_object_key(monkeypatch):
    async def recording(self, *, room_name: str, filepath: str):
        assert room_name == "webcall_wv_123"
        assert filepath.startswith("voice-recordings/")
        return SimpleNamespace(egress_id="egress_1")

    monkeypatch.setattr(LiveKitVoiceProvider, "_start_recording_async", recording)
    provider = LiveKitVoiceProvider(
        livekit_url="wss://voice.example.test",
        api_key="unit_key",
        api_secret="unit_secret",
        recording_bucket="recordings",
    )

    result = provider.execute_action(
        room_name="webcall_wv_123",
        action_type="recording_start",
    )

    assert result.provider_reference == "egress_1"
    assert result.safe_payload == {"artifact_pending": True}
    assert "object_key" not in result.safe_payload
