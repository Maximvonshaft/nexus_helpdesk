from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webcall_ai_deepgram_streaming_tts_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.services.webcall_ai_production.audio.livekit_io import LiveKitAgentIO
from app.services.webcall_ai_production.config import get_webcall_ai_production_settings
from app.services.webcall_ai_production.providers.cancel_token import CancelToken
from app.services.webcall_ai_production.providers.deepgram_streaming_tts import DeepgramStreamingTTSProvider
from app.services.webcall_ai_production.providers.router import get_tts_provider


class FakeDeepgramWebSocket:
    instances: list["FakeDeepgramWebSocket"] = []
    event_order: list[str] = []
    messages: list[bytes | str] = []
    url: str | None = None
    headers: dict | None = None
    chunk_sequence = 0

    def __init__(self):
        self.sent: list[str | bytes] = []
        self.closed = False
        self.messages = list(self.__class__.messages)
        self.__class__.instances.append(self)

    def send(self, payload):
        self.sent.append(payload)

    def recv(self, *, timeout: float):
        if not self.messages:
            raise TimeoutError
        item = self.messages.pop(0)
        if isinstance(item, bytes):
            self.__class__.chunk_sequence += 1
            self.__class__.event_order.append(f"deepgram:chunk:{self.__class__.chunk_sequence}")
        return item

    def close(self):
        self.closed = True


class LazyPublishBackend:
    def __init__(self, *, cancel_token: CancelToken | None = None) -> None:
        self.cancel_token = cancel_token
        self.published: list[bytes] = []

    def connect(self, *, url: str, token: str, room_name: str, participant_identity: str) -> None:
        return None

    def collect_next_customer_utterance(self, *, timeout_seconds: float, max_seconds: float):
        raise AssertionError("not used")

    def publish_ai_audio_stream(self, chunks, *, mime_type: str) -> None:
        for chunk in chunks:
            self.published.append(chunk.audio_bytes)
            FakeDeepgramWebSocket.event_order.append(f"livekit:publish:{len(self.published)}")
            if self.cancel_token is not None and len(self.published) == 1:
                self.cancel_token.cancel("barge_in")

    def publish_ai_audio(self, audio_bytes: bytes, *, mime_type: str) -> None:
        raise AssertionError("fallback publish should not be used")

    def cancel_ai_audio_stream(self, *, reason: str) -> None:
        return None

    def close(self) -> None:
        return None


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    key_file = tmp_path / "deepgram.key"
    key_file.write_text("unit-deepgram-token", encoding="utf-8")
    monkeypatch.setenv("WEBCALL_AI_PRODUCTION_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_AGENT_ENABLED", "true")
    monkeypatch.delenv("WEBCALL_AI_PROVIDER_PROFILE", raising=False)
    monkeypatch.setenv("STT_PROVIDER", "deepgram_streaming")
    monkeypatch.setenv("TTS_PROVIDER", "deepgram_streaming")
    monkeypatch.setenv("STT_API_KEY_FILE", str(key_file))
    monkeypatch.setenv("TTS_API_KEY_FILE", str(key_file))
    monkeypatch.delenv("TTS_ENDPOINT", raising=False)
    monkeypatch.delenv("TTS_MODEL", raising=False)
    monkeypatch.delenv("TTS_ENCODING", raising=False)
    monkeypatch.delenv("TTS_SAMPLE_RATE", raising=False)
    monkeypatch.setattr("app.services.webcall_ai_production.audio.livekit_io.issue_join_token", lambda **kwargs: SimpleNamespace(participant_token="unit-livekit-token"))

    def fake_connect(url, **kwargs):
        FakeDeepgramWebSocket.url = url
        FakeDeepgramWebSocket.headers = kwargs.get("additional_headers") or {}
        return FakeDeepgramWebSocket()

    monkeypatch.setattr("app.services.webcall_ai_production.providers.deepgram_streaming_tts.websocket_connect", fake_connect)
    FakeDeepgramWebSocket.instances = []
    FakeDeepgramWebSocket.event_order = []
    FakeDeepgramWebSocket.chunk_sequence = 0
    FakeDeepgramWebSocket.messages = [b"\x01\x00", b"\x02\x00", json.dumps({"type": "Flushed"})]
    get_webcall_ai_production_settings.cache_clear()
    yield
    get_webcall_ai_production_settings.cache_clear()


def test_deepgram_streaming_tts_provider_config():
    settings = get_webcall_ai_production_settings()

    assert settings.provider_profile == "hybrid"
    assert settings.stt_configured is True
    assert settings.tts_configured is True
    assert settings.tts_provider == "deepgram_streaming"
    assert settings.public_runtime_config()["tts_provider"] == "deepgram_streaming"
    assert isinstance(get_tts_provider("deepgram_streaming"), DeepgramStreamingTTSProvider)


def test_deepgram_streaming_tts_chunk_publish_is_lazy():
    result = DeepgramStreamingTTSProvider().synthesize_lazy("Please provide your tracking number.")
    backend = LazyPublishBackend()
    io = LiveKitAgentIO(room_name="room", participant_identity="ai", ttl_seconds=60, livekit_url="wss://voice.example", backend=backend)

    io.publish_ai_audio_stream(result.audio_stream, mime_type=result.mime_type)

    assert backend.published == [b"\x01\x00", b"\x02\x00"]
    assert FakeDeepgramWebSocket.event_order == [
        "deepgram:chunk:1",
        "livekit:publish:1",
        "deepgram:chunk:2",
        "livekit:publish:2",
    ]


def test_deepgram_streaming_tts_cancel_stops_later_publish():
    token = CancelToken()
    result = DeepgramStreamingTTSProvider().synthesize_lazy("Please provide your tracking number.", cancel_token=token)
    backend = LazyPublishBackend(cancel_token=token)
    io = LiveKitAgentIO(room_name="room", participant_identity="ai", ttl_seconds=60, livekit_url="wss://voice.example", backend=backend)

    io.publish_ai_audio_stream(result.audio_stream, mime_type=result.mime_type)

    ws = FakeDeepgramWebSocket.instances[0]
    sent_controls = [json.loads(item) for item in ws.sent if isinstance(item, str)]
    assert backend.published == [b"\x01\x00"]
    assert {"type": "Clear"} in sent_controls
    assert {"type": "Close"} in sent_controls


def test_deepgram_streaming_tts_secret_redaction():
    result = DeepgramStreamingTTSProvider().synthesize_lazy("hello")
    backend = LazyPublishBackend()
    io = LiveKitAgentIO(room_name="room", participant_identity="ai", ttl_seconds=60, livekit_url="wss://voice.example", backend=backend)

    io.publish_ai_audio_stream(result.audio_stream, mime_type=result.mime_type)

    assert "unit-deepgram-token" not in (FakeDeepgramWebSocket.url or "")
    assert FakeDeepgramWebSocket.headers == {"Authorization": "Token unit-deepgram-token"}
    assert "unit-deepgram-token" not in json.dumps(get_webcall_ai_production_settings().public_runtime_config())
    script = (ROOT.parent / "scripts" / "probe_webcall_ai_spoken_canary.sh").read_text(encoding="utf-8")
    assert "Authorization" in script
    assert "<redacted>" in script
