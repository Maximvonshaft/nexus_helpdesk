from __future__ import annotations

import json
import os
import sys
import urllib.parse
from pathlib import Path
from uuid import uuid4

import pytest

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webcall_ai_streaming_stt_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models, operator_models, tool_models, voice_models, webchat_models  # noqa: E402,F401
from app.db import Base, SessionLocal, engine
from app.services.webcall_ai_production.config import get_webcall_ai_production_settings
from app.services.webcall_ai_production.orchestrator import run_session_turn
from app.services.webcall_ai_production.providers.deepgram_streaming_stt import DeepgramStreamingSTTProvider, parse_deepgram_event
from app.services.webcall_ai_production.providers.router import get_stt_provider
from app.utils.time import utc_now
from app.voice_models import WebchatVoiceAITurn, WebchatVoiceSession, WebchatVoiceTranscriptSegment
from app.webchat_models import WebchatEvent


class FakeDeepgramWebSocket:
    def __init__(self, messages: list[dict]) -> None:
        self.messages = [json.dumps(message) for message in messages]
        self.sent_binary: list[bytes] = []
        self.sent_text: list[str] = []
        self.closed = False

    def send(self, payload):
        if isinstance(payload, bytes):
            self.sent_binary.append(payload)
            return
        self.sent_text.append(str(payload))

    def recv(self, timeout=None):
        if not self.messages:
            raise TimeoutError()
        return self.messages.pop(0)

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def clean_db_and_env(monkeypatch, tmp_path):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    token_file = tmp_path / "deepgram.token"
    token_file.write_text("unit-deepgram-token", encoding="utf-8")
    monkeypatch.setenv("WEBCALL_AI_PRODUCTION_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_AGENT_ENABLED", "true")
    monkeypatch.delenv("WEBCALL_AI_PROVIDER_PROFILE", raising=False)
    monkeypatch.setenv("STT_PROVIDER", "deepgram_streaming")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("TTS_PROVIDER", "fake")
    monkeypatch.setenv("STT_API_KEY_FILE", str(token_file))
    monkeypatch.delenv("STT_ENDPOINT", raising=False)
    monkeypatch.setenv("STT_MODEL", "nova-3")
    monkeypatch.setenv("STT_LANGUAGE", "en")
    monkeypatch.setenv("STT_INTERIM_RESULTS", "true")
    monkeypatch.setenv("STT_ENDPOINTING_MS", "300")
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


def _deepgram_messages(final_text: str = "hello") -> list[dict]:
    return [
        {
            "type": "Results",
            "is_final": False,
            "speech_final": False,
            "metadata": {"request_id": "dg-req-1"},
            "channel": {"alternatives": [{"transcript": "hel", "confidence": 0.52}]},
        },
        {
            "type": "Results",
            "is_final": True,
            "speech_final": True,
            "language": "en",
            "metadata": {"request_id": "dg-req-1"},
            "channel": {"alternatives": [{"transcript": final_text, "confidence": 0.96}]},
        },
    ]


def test_deepgram_event_parser_maps_partial_and_final_results():
    partial = parse_deepgram_event(json.dumps(_deepgram_messages()[0]), provider="deepgram_streaming")
    final = parse_deepgram_event(json.dumps(_deepgram_messages()[1]), provider="deepgram_streaming")

    assert partial.type == "partial"
    assert partial.text == "hel"
    assert partial.is_final is False
    assert final.type == "final"
    assert final.text == "hello"
    assert final.speech_final is True
    assert final.confidence == 96


def test_streaming_stt_config_auto_selects_hybrid_profile():
    settings = get_webcall_ai_production_settings()

    assert settings.provider_profile == "hybrid"
    assert settings.stt_provider == "deepgram_streaming"
    assert settings.stt_configured is True
    assert settings.provider_configured is True
    assert settings.public_runtime_config()["stt_provider"] == "deepgram_streaming"


def test_provider_router_returns_deepgram_streaming_provider():
    assert isinstance(get_stt_provider("deepgram_streaming"), DeepgramStreamingSTTProvider)


def test_deepgram_streaming_provider_sends_pcm_frames_and_finalize(monkeypatch):
    fake_ws = FakeDeepgramWebSocket(_deepgram_messages("where is my parcel"))
    captured = {}

    def fake_connect(uri, **kwargs):
        captured["uri"] = uri
        captured["kwargs"] = kwargs
        return fake_ws

    monkeypatch.setattr("app.services.webcall_ai_production.providers.deepgram_streaming_stt.websocket_connect", fake_connect)
    provider = DeepgramStreamingSTTProvider()

    result = provider.transcribe(b"\x01\x00" * 640, language="en", sample_rate=16000, channels=1, mime_type="audio/pcm")

    assert result.text == "where is my parcel"
    assert result.confidence == 96
    assert result.provider_name == "deepgram_streaming"
    assert len(fake_ws.sent_binary) >= 1
    assert json.loads(fake_ws.sent_text[-1]) == {"type": "Finalize"}
    assert fake_ws.closed is True
    parsed = urllib.parse.urlparse(captured["uri"])
    query = urllib.parse.parse_qs(parsed.query)
    assert parsed.scheme == "wss"
    assert query["model"] == ["nova-3"]
    assert query["encoding"] == ["linear16"]
    assert query["sample_rate"] == ["16000"]
    assert query["channels"] == ["1"]
    assert query["interim_results"] == ["true"]
    assert query["endpointing"] == ["300"]
    assert query["language"] == ["en"]
    assert captured["kwargs"]["additional_headers"]["Authorization"] == "Token unit-deepgram-token"


def test_session_turn_persists_streaming_stt_final_transcript(db, monkeypatch):
    fake_ws = FakeDeepgramWebSocket(_deepgram_messages("hello"))
    monkeypatch.setattr(
        "app.services.webcall_ai_production.providers.deepgram_streaming_stt.websocket_connect",
        lambda *args, **kwargs: fake_ws,
    )
    session = _voice_session(db)

    result = run_session_turn(
        db,
        session=session,
        audio=b"\x01\x00" * 640,
        worker_id="worker-streaming-stt",
        language="en",
        sample_rate=16000,
        channels=1,
        mime_type="audio/pcm",
    )

    transcript = db.query(WebchatVoiceTranscriptSegment).one()
    turn = db.query(WebchatVoiceAITurn).one()
    event_types = [row.event_type for row in db.query(WebchatEvent).filter(WebchatEvent.conversation_id == session.conversation_id).all()]

    assert result["transcript"]["text"] == "hello"
    assert transcript.provider == "deepgram_streaming"
    assert transcript.text_redacted == "hello"
    assert turn.stt_provider == "deepgram_streaming"
    assert turn.customer_text_redacted == "hello"
    assert "webcall_ai.transcript.final" in event_types
