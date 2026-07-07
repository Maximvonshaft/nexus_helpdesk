from __future__ import annotations

import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webcall_ai_audio_evidence_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models, operator_models, tool_models, voice_models, webchat_models  # noqa: E402,F401
from app.db import Base, SessionLocal, engine
from app.services.webcall_ai_production.audio.livekit_io import SDKLiveKitRTCBackend
from app.services.webcall_ai_production.audio.stats import (
    AUDIO_TRACK_MUTED,
    DEEPGRAM_EMPTY_TRANSCRIPT,
    NO_PCM_FRAMES,
    NO_REMOTE_AUDIO_TRACK,
    PCM_SILENT,
    PCM_TOO_SHORT,
    analyze_pcm16_audio,
)
from app.services.webcall_ai_production.config import get_webcall_ai_production_settings
from app.services.webcall_ai_production.orchestrator import run_session_turn
from app.services.webcall_ai_production.providers.base import ProviderError
from app.utils.time import utc_now
from app.voice_models import WebchatVoiceSession
from app.webchat_models import WebchatEvent


class EmptyTranscriptSTTProvider:
    provider_name = "deepgram_streaming"

    def transcribe(self, *args, **kwargs):
        raise ProviderError(self.provider_name, "stt_empty_transcript", "Streaming STT returned no transcript")


class FakePublication:
    sid = "TRK_audio_123"
    muted = False


class FakeParticipant:
    identity = "visitor_voice_123"


class FakeTrack:
    sid = "track-fallback"
    muted = False


@pytest.fixture(autouse=True)
def clean_db_and_env(monkeypatch):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("WEBCALL_AI_PRODUCTION_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_AGENT_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_PROVIDER_PROFILE", "fake")
    monkeypatch.setenv("WEBCHAT_VOICE_PROVIDER", "mock")
    monkeypatch.setenv("WEBCHAT_VOICE_ENABLED", "true")
    monkeypatch.setenv("STT_PROVIDER", "fake")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("TTS_PROVIDER", "fake")
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


def _voice_session(db) -> WebchatVoiceSession:
    now = utc_now()
    session = WebchatVoiceSession(
        public_id=f"voice_{uuid4().hex}",
        conversation_id=1,
        ticket_id=1,
        provider="livekit",
        provider_room_name=f"room_{uuid4().hex}",
        status="active",
        mode="livekit_ai_agent",
        ai_language="en",
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def test_agent_remote_track_subscription_telemetry_payload():
    events: list[tuple[str, dict]] = []
    backend = SDKLiveKitRTCBackend(telemetry_callback=lambda event_type, payload: events.append((event_type, payload)))

    backend._record_remote_track_subscribed(track=FakeTrack(), publication=FakePublication(), participant=FakeParticipant())

    assert events == [
        (
            "webcall_ai.livekit.remote_track_subscribed",
            {
                "participant_identity": "visitor_voice_123",
                "track_sid": "TRK_audio_123",
                "track_kind": "audio",
                "track_muted": False,
            },
        )
    ]


def test_silent_pcm_classification():
    stats = analyze_pcm16_audio(b"\x00\x00" * 24000, sample_rate=48000, channels=1, frame_count=25)

    assert stats.audio_ms == 500
    assert stats.pcm_bytes == 48000
    assert stats.rms_min == 0
    assert stats.rms_avg == 0
    assert stats.rms_max == 0
    assert stats.classification == PCM_SILENT


def test_empty_audio_classification_reasons_are_distinct():
    assert analyze_pcm16_audio(b"", sample_rate=48000, channels=1, frame_count=0, remote_track_seen=False).classification == NO_REMOTE_AUDIO_TRACK
    assert analyze_pcm16_audio(b"", sample_rate=48000, channels=1, frame_count=0, remote_track_seen=True, audio_track_muted=True).classification == AUDIO_TRACK_MUTED
    assert analyze_pcm16_audio(b"", sample_rate=48000, channels=1, frame_count=0, remote_track_seen=True).classification == NO_PCM_FRAMES
    assert analyze_pcm16_audio((1200).to_bytes(2, "little", signed=True) * 2400, sample_rate=48000, channels=1, frame_count=3).classification == PCM_TOO_SHORT


def test_non_empty_pcm_but_empty_stt_is_classified_as_deepgram_empty(db, monkeypatch):
    monkeypatch.setattr("app.services.webcall_ai_production.orchestrator.get_stt_provider", lambda name: EmptyTranscriptSTTProvider())
    session = _voice_session(db)
    non_silent_pcm = (1200).to_bytes(2, "little", signed=True) * 24000

    result = run_session_turn(
        db,
        session=session,
        audio=non_silent_pcm,
        worker_id="worker-audio-evidence",
        language="en",
        sample_rate=48000,
        channels=1,
        mime_type="audio/pcm",
        audio_stats={
            "participant_identity": "visitor_voice_123",
            "track_sid": "TRK_audio_123",
            "frame_count": 25,
            "audio_ms": 500,
            "pcm_bytes": len(non_silent_pcm),
            "sample_rate": 48000,
            "channels": 1,
            "rms_min": 1200,
            "rms_avg": 1200,
            "rms_max": 1200,
            "audio_input_classification": "audio_present",
        },
    )

    events = db.query(WebchatEvent).filter(WebchatEvent.conversation_id == session.conversation_id).order_by(WebchatEvent.id.asc()).all()
    audio_input = next(event for event in events if event.event_type == "webcall_ai.stt.audio_input_stats")
    empty = next(event for event in events if event.event_type == "webcall_ai.stt.empty_with_audio_stats")

    assert result["handoff_required"] is False
    assert '"raw_audio"' not in (audio_input.payload_json or "").lower()
    assert '"tracking_number"' not in (audio_input.payload_json or "").lower()
    assert '"participant_identity": "visitor_voice_123"' in (audio_input.payload_json or "")
    assert '"track_sid": "TRK_audio_123"' in (audio_input.payload_json or "")
    assert f'"empty_reason": "{DEEPGRAM_EMPTY_TRANSCRIPT}"' in (empty.payload_json or "")
    assert '"pcm_bytes": 48000' in (empty.payload_json or "")
