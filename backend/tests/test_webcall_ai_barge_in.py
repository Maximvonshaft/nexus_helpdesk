from __future__ import annotations

import asyncio
import json
import os
import sys
import types
from pathlib import Path
from uuid import uuid4

import pytest

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webcall_ai_barge_in_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models, operator_models, tool_models, voice_models, webchat_fast_models, webchat_models  # noqa: E402,F401
from app.db import Base, SessionLocal, engine
from app.services.webcall_ai_production.agent_session_claims import AI_STATUS_CLAIMED
from app.services.webcall_ai_production.agent_worker import run_claimed_session_loop
from app.services.webcall_ai_production.audio.livekit_io import BargeInInterrupted, LiveKitMediaTurn, PCMFrame, SDKLiveKitRTCBackend
from app.services.webcall_ai_production.config import get_webcall_ai_production_settings
from app.services.webcall_ai_production.providers.base import TTSResult
from app.services.webcall_ai_production.providers.streaming_tts_base import TTSChunk
from app.utils.time import utc_now
from app.webchat_models import WebchatEvent
from app.voice_models import WebchatVoiceSession


class FakeAudioFrame:
    def __init__(self, *, data: bytes, sample_rate: int, num_channels: int, samples_per_channel: int) -> None:
        self.data = data
        self.sample_rate = sample_rate
        self.num_channels = num_channels
        self.samples_per_channel = samples_per_channel


class FakeAudioSource:
    def __init__(self) -> None:
        self.frames: list[FakeAudioFrame] = []

    async def capture_frame(self, frame: FakeAudioFrame) -> None:
        self.frames.append(frame)


class ChunkTTSProvider:
    provider_name = "chunk_tts"

    def synthesize(self, text: str, *, language: str | None = None) -> TTSResult:
        chunks = (
            TTSChunk(audio_bytes=b"\x01\x00\x02\x00", mime_type="audio/pcm", sample_rate=24000, channels=1, provider_name=self.provider_name),
            TTSChunk(audio_bytes=b"\x03\x00\x04\x00", mime_type="audio/pcm", sample_rate=24000, channels=1, provider_name=self.provider_name),
        )
        return TTSResult(
            audio_bytes=b"".join(chunk.audio_bytes for chunk in chunks),
            mime_type="audio/pcm",
            text=text,
            provider_name=self.provider_name,
            audio_chunks=chunks,
        )


class BargeInAgentIO:
    def __init__(self, utterances: list[bytes]) -> None:
        self.utterances = list(utterances)
        self.connected = False
        self.closed = False
        self.publish_count = 0
        self.cancel_reasons: list[str] = []
        self.published_streams: list[list[bytes]] = []

    def connect(self) -> None:
        self.connected = True

    def collect_next_customer_utterance(self, *, timeout_seconds=20.0, max_seconds=12.0):
        if not self.utterances:
            raise RuntimeError("no more utterances")
        return LiveKitMediaTurn(audio_bytes=self.utterances.pop(0), sample_rate=48000, channels=1, mime_type="audio/pcm", language="en")

    def publish_ai_audio_stream(self, chunks, *, mime_type: str) -> None:
        self.publish_count += 1
        if self.publish_count == 2:
            raise BargeInInterrupted(speech_ms=320, buffered_frames=2)
        self.published_streams.append([chunk.audio_bytes for chunk in chunks])

    def publish_ai_audio(self, audio_bytes: bytes, *, mime_type: str) -> None:
        self.publish_count += 1

    def cancel_ai_audio_stream(self, *, reason: str) -> None:
        self.cancel_reasons.append(reason)

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def clean_db_and_env(monkeypatch):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("WEBCALL_AI_PRODUCTION_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_AGENT_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_PROVIDER_PROFILE", "fake")
    monkeypatch.setenv("WEBCALL_AI_MAX_SESSION_SECONDS", "60")
    monkeypatch.setenv("WEBCHAT_VOICE_PROVIDER", "mock")
    monkeypatch.setenv("WEBCHAT_VOICE_ENABLED", "true")
    monkeypatch.setenv("STT_PROVIDER", "fake")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("TTS_PROVIDER", "fake")
    monkeypatch.setenv("WEBCALL_AI_BARGE_IN_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_BARGE_IN_MIN_SPEECH_MS", "10")
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


def test_livekit_publish_stream_interrupts_and_preserves_barge_in_audio(monkeypatch):
    fake_livekit = types.SimpleNamespace(rtc=types.SimpleNamespace(AudioFrame=FakeAudioFrame))
    monkeypatch.setitem(sys.modules, "livekit", fake_livekit)
    monkeypatch.setenv("WEBCALL_AI_MIN_UTTERANCE_SECONDS", "0")
    monkeypatch.setenv("WEBCALL_AI_SILENCE_END_MS", "0")
    backend = SDKLiveKitRTCBackend()
    backend._audio_queue = asyncio.Queue()
    backend._audio_source = FakeAudioSource()
    speech = (1200).to_bytes(2, "little", signed=True) * 960
    backend._audio_queue.put_nowait(PCMFrame(data=speech, sample_rate=48000, channels=1))
    backend._audio_queue.put_nowait(PCMFrame(data=speech, sample_rate=48000, channels=1))
    tts_chunk = TTSChunk(audio_bytes=b"\x00\x00" * 960, mime_type="audio/pcm", sample_rate=24000, channels=1)

    with pytest.raises(BargeInInterrupted) as exc_info:
        asyncio.run(backend._publish_ai_audio_stream((tts_chunk,), mime_type="audio/pcm"))

    turn = asyncio.run(backend._collect_next_customer_utterance(timeout_seconds=0.01, max_seconds=0.05))

    assert exc_info.value.speech_ms >= 10
    assert exc_info.value.buffered_frames == 2
    assert turn.audio_bytes == speech
    assert turn.sample_rate == 48000
    assert turn.channels == 1


def test_agent_loop_records_interruption_and_returns_to_listening(db, monkeypatch):
    monkeypatch.setattr("app.services.webcall_ai_production.orchestrator.get_tts_provider", lambda name: ChunkTTSProvider())
    session = _claimed_session(db)
    io = BargeInAgentIO([b"hello", b"Please track SF123456789CN"])

    result = run_claimed_session_loop(session.id, worker_id="worker-test", io=io)

    events = db.query(WebchatEvent).order_by(WebchatEvent.id.asc()).all()
    event_types = [event.event_type for event in events]
    interrupted_index = event_types.index("webcall_ai.response.interrupted")

    assert result["status"] == "handoff_required"
    assert io.connected is True
    assert io.closed is True
    assert io.cancel_reasons == ["barge_in"]
    assert len(io.published_streams) >= 2
    assert "webcall_ai.response.interrupted" in event_types
    assert "webcall_ai.response.publish_failed" not in event_types
    assert "webcall_ai.agent.listening" in event_types[interrupted_index + 1 :]
    interrupted = events[interrupted_index]
    payload = json.loads(interrupted.payload_json)
    assert payload["reason"] == "barge_in"
    assert payload["speech_ms"] == 320
    assert payload["buffered_frames"] == 2
