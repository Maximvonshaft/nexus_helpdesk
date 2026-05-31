from __future__ import annotations

import base64
import json
import os
import sys
from types import SimpleNamespace
from pathlib import Path
from uuid import uuid4

import pytest

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webcall_ai_streaming_tts_publish_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models, operator_models, tool_models, voice_models, webchat_fast_models, webchat_models  # noqa: E402,F401
from app.db import Base, SessionLocal, engine
from app.services.webcall_ai_production.agent_session_claims import AI_STATUS_CLAIMED
from app.services.webcall_ai_production.agent_worker import run_claimed_session_loop
from app.services.webcall_ai_production.audio.livekit_io import LiveKitAgentIO, LiveKitMediaTurn
from app.services.webcall_ai_production.config import get_webcall_ai_production_settings
from app.services.webcall_ai_production.providers.cartesia_streaming_tts import CartesiaStreamingTTSProvider, parse_cartesia_sse_line
from app.services.webcall_ai_production.providers.router import get_tts_provider
from app.services.webcall_ai_production.providers.streaming_tts_base import TTSChunk
from app.utils.time import utc_now
from app.voice_models import WebchatVoiceAITurn, WebchatVoiceSession
from app.webchat_models import WebchatEvent


class FakeCartesiaResponse:
    chunk_sequence = 0

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        return None

    def iter_lines(self):
        for line in self.lines:
            if '"type":"chunk"' in line:
                FakeCartesiaResponse.chunk_sequence += 1
                FakeCartesiaClient.event_order.append(f"cartesia:chunk:{FakeCartesiaResponse.chunk_sequence}")
            yield line


class FakeCartesiaClient:
    calls: list[dict] = []
    event_order: list[str] = []

    def __init__(self, timeout):
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def stream(self, method, endpoint, *, headers, json):
        self.calls.append({"method": method, "endpoint": endpoint, "headers": headers, "json": json})
        return FakeCartesiaResponse(_cartesia_sse_lines())


class FakeStreamingAgentIO:
    def __init__(self, utterances: list[bytes]):
        self.utterances = list(utterances)
        self.connected = False
        self.closed = False
        self.published_streams: list[list[bytes]] = []
        self.published_fallback: list[tuple[bytes, str]] = []

    def connect(self):
        self.connected = True

    def collect_next_customer_utterance(self, *, timeout_seconds=20.0, max_seconds=12.0):
        if not self.utterances:
            raise RuntimeError("no more utterances")
        return LiveKitMediaTurn(audio_bytes=self.utterances.pop(0), sample_rate=48000, channels=1, mime_type="audio/pcm", language="en")

    def publish_ai_audio_stream(self, chunks, *, mime_type: str):
        stream: list[bytes] = []
        self.published_streams.append(stream)
        for chunk in chunks:
            stream.append(chunk.audio_bytes)
            FakeCartesiaClient.event_order.append(f"livekit:publish:{len(stream)}")

    def publish_ai_audio(self, audio_bytes: bytes, *, mime_type: str):
        self.published_fallback.append((audio_bytes, mime_type))

    def close(self):
        self.closed = True


class LazyStreamBackend:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def connect(self, *, url: str, token: str, room_name: str, participant_identity: str) -> None:
        self.events.append("backend:connect")

    def collect_next_customer_utterance(self, *, timeout_seconds: float, max_seconds: float):
        raise AssertionError("not used")

    def publish_ai_audio_stream(self, chunks, *, mime_type: str) -> None:
        for chunk in chunks:
            self.events.append(f"backend:publish:{chunk.audio_bytes!r}")

    def publish_ai_audio(self, audio_bytes: bytes, *, mime_type: str) -> None:
        raise AssertionError("fallback publish should not be used")

    def cancel_ai_audio_stream(self, *, reason: str) -> None:
        self.events.append(f"backend:cancel:{reason}")

    def close(self) -> None:
        self.events.append("backend:close")


@pytest.fixture(autouse=True)
def clean_db_and_env(monkeypatch, tmp_path):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    token_file = tmp_path / "cartesia.token"
    token_file.write_text("unit-cartesia-token", encoding="utf-8")
    monkeypatch.setenv("WEBCALL_AI_PRODUCTION_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_AGENT_ENABLED", "true")
    monkeypatch.delenv("WEBCALL_AI_PROVIDER_PROFILE", raising=False)
    monkeypatch.setenv("STT_PROVIDER", "fake")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("TTS_PROVIDER", "cartesia_streaming")
    monkeypatch.setenv("TTS_API_KEY_FILE", str(token_file))
    monkeypatch.setenv("TTS_VOICE_ID", "voice-unit")
    monkeypatch.setenv("TTS_MODEL", "sonic-3.5")
    monkeypatch.setenv("TTS_SAMPLE_RATE", "24000")
    monkeypatch.setenv("CARTESIA_VERSION", "2026-03-01")
    monkeypatch.delenv("TTS_ENDPOINT", raising=False)
    FakeCartesiaClient.calls = []
    FakeCartesiaClient.event_order = []
    FakeCartesiaResponse.chunk_sequence = 0
    get_webcall_ai_production_settings.cache_clear()
    yield
    Base.metadata.drop_all(bind=engine)
    get_webcall_ai_production_settings.cache_clear()


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _cartesia_sse_lines() -> list[str]:
    first = base64.b64encode(b"\x01\x00\x02\x00").decode("ascii")
    second = base64.b64encode(b"\x03\x00\x04\x00").decode("ascii")
    return [
        f'data: {{"type":"chunk","done":false,"step_time":11,"context_id":"ctx-1","data":"{first}"}}',
        "",
        f'data: {{"type":"chunk","done":false,"step_time":7,"context_id":"ctx-1","data":"{second}"}}',
        "",
        'data: {"type":"done","done":true,"context_id":"ctx-1"}',
        "",
    ]


def _claimed_session(db) -> WebchatVoiceSession:
    now = utc_now()
    session = WebchatVoiceSession(
        public_id=f"voice_{uuid4().hex}",
        conversation_id=1,
        ticket_id=1,
        provider="livekit",
        provider_room_name=f"room_{uuid4().hex}",
        mode="livekit_ai_agent",
        status="created",
        ai_agent_status=AI_STATUS_CLAIMED,
        ai_agent_worker_id="worker-test",
        ai_agent_claimed_at=now,
        ai_agent_last_heartbeat_at=now,
        ai_agent_lease_expires_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def test_cartesia_sse_parser_decodes_audio_chunk_and_done():
    chunk_event = parse_cartesia_sse_line(_cartesia_sse_lines()[0], sample_rate=24000, channels=1)
    done_event = parse_cartesia_sse_line(_cartesia_sse_lines()[4], sample_rate=24000, channels=1)

    assert chunk_event.chunk.audio_bytes == b"\x01\x00\x02\x00"
    assert chunk_event.chunk.mime_type == "audio/pcm"
    assert chunk_event.chunk.sample_rate == 24000
    assert chunk_event.chunk.channels == 1
    assert chunk_event.chunk.provider_latency_ms == 11
    assert done_event.done is True


def test_streaming_tts_config_auto_selects_hybrid_profile():
    settings = get_webcall_ai_production_settings()

    assert settings.provider_profile == "hybrid"
    assert settings.tts_provider == "cartesia_streaming"
    assert settings.tts_configured is True
    assert settings.provider_configured is True
    assert settings.public_runtime_config()["tts_provider"] == "cartesia_streaming"


def test_provider_router_returns_cartesia_streaming_provider():
    assert isinstance(get_tts_provider("cartesia_streaming"), CartesiaStreamingTTSProvider)


def test_livekit_agent_io_does_not_materialize_stream_before_backend(monkeypatch):
    events: list[str] = []
    monkeypatch.setattr(
        "app.services.webcall_ai_production.audio.livekit_io.issue_join_token",
        lambda **kwargs: SimpleNamespace(participant_token="unit-token"),
    )

    def chunks():
        events.append("provider:chunk:1")
        yield TTSChunk(audio_bytes=b"one", mime_type="audio/pcm", sample_rate=24000, channels=1)
        events.append("provider:chunk:2")
        yield TTSChunk(audio_bytes=b"two", mime_type="audio/pcm", sample_rate=24000, channels=1)

    io = LiveKitAgentIO(room_name="room", participant_identity="ai", ttl_seconds=60, livekit_url="wss://voice.example", backend=LazyStreamBackend(events))

    io.publish_ai_audio_stream(chunks(), mime_type="audio/pcm")

    assert events == [
        "backend:connect",
        "provider:chunk:1",
        "backend:publish:b'one'",
        "provider:chunk:2",
        "backend:publish:b'two'",
    ]


def test_cartesia_streaming_tts_collects_chunks_and_builds_request(monkeypatch):
    monkeypatch.setattr("app.services.webcall_ai_production.providers.cartesia_streaming_tts.httpx.Client", FakeCartesiaClient)

    result = CartesiaStreamingTTSProvider().synthesize("Please provide your tracking number.", language="en")

    assert result.audio_bytes == b"\x01\x00\x02\x00\x03\x00\x04\x00"
    assert result.mime_type == "audio/pcm"
    assert result.provider_name == "cartesia_streaming"
    assert len(result.audio_chunks) == 2
    call = FakeCartesiaClient.calls[0]
    assert call["method"] == "POST"
    assert call["endpoint"] == "https://api.cartesia.ai/tts/sse"
    assert call["headers"]["Authorization"] == "Bearer unit-cartesia-token"
    assert call["headers"]["Cartesia-Version"] == "2026-03-01"
    assert call["json"]["model_id"] == "sonic-3.5"
    assert call["json"]["voice"] == {"id": "voice-unit"}
    assert call["json"]["output_format"] == {"container": "RAW", "encoding": "pcm_s16le", "sample_rate": 24000}
    assert call["json"]["language"] == "en"


def test_agent_loop_publishes_cartesia_chunks_through_stream_path(db, monkeypatch):
    monkeypatch.setattr("app.services.webcall_ai_production.providers.cartesia_streaming_tts.httpx.Client", FakeCartesiaClient)
    session = _claimed_session(db)
    io = FakeStreamingAgentIO([b"Please track SF123456789CN"])

    result = run_claimed_session_loop(session.id, worker_id="worker-test", io=io)

    turns = db.query(WebchatVoiceAITurn).order_by(WebchatVoiceAITurn.id.asc()).all()
    event_types = [event.event_type for event in db.query(WebchatEvent).order_by(WebchatEvent.id.asc()).all()]

    assert result["status"] == "handoff_required"
    assert io.connected is True
    assert io.closed is True
    assert len(io.published_streams) >= 2
    assert io.published_fallback == []
    assert all(stream == [b"\x01\x00\x02\x00", b"\x03\x00\x04\x00"] for stream in io.published_streams)
    assert FakeCartesiaClient.event_order[:4] == [
        "cartesia:chunk:1",
        "livekit:publish:1",
        "cartesia:chunk:2",
        "livekit:publish:2",
    ]
    assert {turn.tts_provider for turn in turns} == {"cartesia_streaming"}
    assert "webcall_ai.response.spoken" in event_types
    assert len(FakeCartesiaClient.calls) >= 2
