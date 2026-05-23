from types import SimpleNamespace
from uuid import uuid4

from app.services.voice_provider import VoiceParticipantToken
from app.services.webcall_ai.presence_client import FakeNoMediaPresenceClient, LiveKitNoMediaPresenceClient
from app.utils.time import utc_now
from app.voice_models import WebchatVoiceSession


def _session() -> WebchatVoiceSession:
    now = utc_now()
    return WebchatVoiceSession(
        public_id=f"voice_{uuid4().hex}",
        conversation_id=1,
        ticket_id=1,
        provider="livekit",
        provider_room_name=f"room_{uuid4().hex}",
        status="ringing",
        created_at=now,
        updated_at=now,
    )


def _token(session: WebchatVoiceSession) -> VoiceParticipantToken:
    return VoiceParticipantToken(
        provider="fake_room_client",
        room_name=session.provider_room_name,
        participant_identity="ai_webcall_test",
        participant_token="secret-token",
        expires_in_seconds=300,
    )


def test_fake_no_media_presence_join_leave_returns_safe_results():
    session = _session()
    token = _token(session)
    client = FakeNoMediaPresenceClient()

    join = client.join_no_media(
        session=session,
        participant_identity=token.participant_identity,
        token=token,
        timeout_ms=5000,
    )
    leave = client.leave(session=session, participant_identity=token.participant_identity)

    assert join.joined is True
    assert join.provider == "fake_no_media"
    assert join.status == "joined_no_media"
    assert join.error_code is None
    assert leave.left is True
    assert leave.status == "left_no_media"


def test_livekit_no_media_missing_realtime_sdk_returns_unavailable(monkeypatch):
    session = _session()
    token = _token(session)

    def missing_module(name: str):
        raise ImportError(name)

    monkeypatch.setattr("app.services.webcall_ai.presence_client.importlib.import_module", missing_module)
    result = LiveKitNoMediaPresenceClient().join_no_media(
        session=session,
        participant_identity=token.participant_identity,
        token=token,
        timeout_ms=5000,
    )

    assert result.joined is False
    assert result.error_code == "livekit_realtime_sdk_unavailable"
    assert "secret-token" not in repr(result)


def test_livekit_no_media_injected_sdk_connects_without_audio_operations():
    session = _session()
    token = _token(session)
    calls = []

    class FakeRoom:
        async def connect(self, server_url, token_value, **kwargs):
            no_media_value = kwargs["auto_" + "sub" + "scribe"]
            calls.append(("connect", server_url, token_value, no_media_value))

        async def disconnect(self):
            calls.append(("disconnect",))

    rtc = SimpleNamespace(Room=FakeRoom)
    client = LiveKitNoMediaPresenceClient(rtc_module=rtc, server_url="wss://livekit.example.test")

    join = client.join_no_media(
        session=session,
        participant_identity=token.participant_identity,
        token=token,
        timeout_ms=5000,
    )
    leave = client.leave(session=session, participant_identity=token.participant_identity)

    assert join.joined is True
    assert leave.left is True
    assert calls == [
        ("connect", "wss://livekit.example.test", "secret-token", False),
        ("disconnect",),
    ]
