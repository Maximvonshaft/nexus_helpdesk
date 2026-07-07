from __future__ import annotations

import json
import os
import sys
import urllib.parse
from pathlib import Path
from uuid import uuid4

import pytest

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webcall_ai_stt_quality_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models, operator_models, tool_models, voice_models, webchat_models  # noqa: E402,F401
from app.db import Base, SessionLocal, engine
from app.services.webcall_ai_production.config import get_webcall_ai_production_settings
from app.services.webcall_ai_production.event_service import write_event
from app.services.webcall_ai_production.orchestrator import build_handoff_turn, run_session_turn
from app.utils.time import utc_now
from app.voice_models import WebchatVoiceSession
from app.webchat_models import WebchatEvent


class FakeDeepgramWebSocket:
    final_text = "hello"
    connections: list["FakeDeepgramWebSocket"] = []
    uris: list[str] = []
    headers: list[dict] = []

    def __init__(self) -> None:
        self.messages = [
            json.dumps(
                {
                    "type": "Results",
                    "is_final": True,
                    "speech_final": True,
                    "language": "en",
                    "metadata": {"request_id": f"dg-req-{len(self.connections) + 1}"},
                    "channel": {"alternatives": [{"transcript": self.final_text, "confidence": 0.91}]},
                }
            )
        ]
        self.sent_binary: list[bytes] = []
        self.sent_text: list[str] = []
        self.closed = False
        self.connections.append(self)

    def send(self, payload):
        if isinstance(payload, bytes):
            self.sent_binary.append(payload)
        else:
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
    monkeypatch.setenv("WEBCHAT_VOICE_PROVIDER", "mock")
    monkeypatch.setenv("WEBCHAT_VOICE_ENABLED", "true")
    monkeypatch.setenv("STT_PROVIDER", "deepgram_streaming")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("TTS_PROVIDER", "fake")
    monkeypatch.setenv("STT_API_KEY_FILE", str(token_file))
    monkeypatch.setenv("STT_MODEL", "nova-3")
    monkeypatch.setenv("STT_LANGUAGE", "en")
    monkeypatch.setenv("STT_INTERIM_RESULTS", "true")
    monkeypatch.setenv("STT_ENDPOINTING_MS", "300")
    monkeypatch.setenv("WEBCALL_AI_STT_LOW_RMS_THRESHOLD", "900")
    monkeypatch.delenv("STT_ENCODING", raising=False)
    monkeypatch.delenv("STT_ENDPOINT", raising=False)
    monkeypatch.delenv("WEBCALL_AI_STT_SHADOW_CANARY_ENABLED", raising=False)
    monkeypatch.delenv("WEBCALL_AI_STT_NORMALIZE_PCM_ENABLED", raising=False)

    def fake_connect(uri, **kwargs):
        FakeDeepgramWebSocket.uris.append(uri)
        FakeDeepgramWebSocket.headers.append(kwargs.get("additional_headers") or {})
        return FakeDeepgramWebSocket()

    monkeypatch.setattr("app.services.webcall_ai_production.providers.deepgram_streaming_stt.websocket_connect", fake_connect)
    FakeDeepgramWebSocket.final_text = "hello"
    FakeDeepgramWebSocket.connections = []
    FakeDeepgramWebSocket.uris = []
    FakeDeepgramWebSocket.headers = []
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


def _pcm(amplitude: int, *, sample_rate: int = 48000, ms: int = 1000) -> bytes:
    samples = int(sample_rate * ms / 1000)
    return int(amplitude).to_bytes(2, "little", signed=True) * samples


def _event_payloads(db, session: WebchatVoiceSession, event_type: str) -> list[dict]:
    rows = (
        db.query(WebchatEvent)
        .filter(WebchatEvent.conversation_id == session.conversation_id, WebchatEvent.event_type == event_type)
        .order_by(WebchatEvent.id.asc())
        .all()
    )
    return [json.loads(row.payload_json or "{}") for row in rows]


def test_stt_request_contract_matches_48k_mono_linear16(db):
    FakeDeepgramWebSocket.final_text = "banana tracking"
    session = _voice_session(db)

    run_session_turn(
        db,
        session=session,
        audio=_pcm(1200, ms=4000),
        worker_id="worker-stt-quality",
        language="en",
        sample_rate=48000,
        channels=1,
        mime_type="audio/pcm",
    )

    contract = _event_payloads(db, session, "webcall_ai.stt.request_contract")[0]
    parsed = urllib.parse.urlparse(FakeDeepgramWebSocket.uris[0])
    query = urllib.parse.parse_qs(parsed.query)
    payload_text = json.dumps(contract)

    assert contract["contract_match"] is True
    assert contract["request_encoding"] == "linear16"
    assert contract["request_sample_rate"] == 48000
    assert contract["request_channels"] == 1
    assert contract["input_pcm_sample_rate"] == 48000
    assert contract["input_pcm_channels"] == 1
    assert contract["input_audio_ms"] == 4000
    assert query["encoding"] == ["linear16"]
    assert query["sample_rate"] == ["48000"]
    assert query["channels"] == ["1"]
    assert "unit-deepgram-token" not in payload_text
    assert "Authorization" not in payload_text


def test_contract_mismatch_is_recorded_without_secret_leak(db, monkeypatch):
    monkeypatch.setenv("STT_ENCODING", "mulaw")
    get_webcall_ai_production_settings.cache_clear()
    session = _voice_session(db)

    run_session_turn(
        db,
        session=session,
        audio=_pcm(1200, ms=1000),
        worker_id="worker-stt-quality",
        language="en",
        sample_rate=48000,
        channels=1,
        mime_type="audio/pcm",
    )

    contract = _event_payloads(db, session, "webcall_ai.stt.request_contract")[0]
    assert contract["contract_match"] is False
    assert contract["request_encoding"] == "mulaw"
    assert "request_encoding_not_linear16" in contract["mismatch_reasons"]
    assert "unit-deepgram-token" not in json.dumps(contract)


def test_shadow_canary_writes_redacted_results_without_raw_audio_or_tokens(db, monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_STT_SHADOW_CANARY_ENABLED", "true")
    get_webcall_ai_production_settings.cache_clear()
    FakeDeepgramWebSocket.final_text = "BANANA SPEEDAF TEST my tracking number ABC123456789"
    session = _voice_session(db)

    run_session_turn(
        db,
        session=session,
        audio=_pcm(1300, ms=4000),
        worker_id="worker-stt-quality",
        language="en",
        sample_rate=48000,
        channels=1,
        mime_type="audio/pcm",
    )

    shadow_results = _event_payloads(db, session, "webcall_ai.stt.shadow_result")
    winner = _event_payloads(db, session, "webcall_ai.stt.shadow_winner")[0]
    payload_text = json.dumps({"results": shadow_results, "winner": winner})

    assert {item["shadow_candidate"] for item in shadow_results} == {
        "current_production_config",
        "explicit_48k_linear16_en",
        "explicit_48k_linear16_en_with_smart_format",
    }
    assert winner["ok"] is True
    assert "unit-deepgram-token" not in payload_text
    assert "raw_audio" not in payload_text.lower()
    assert "ABC123456789" not in payload_text
    assert "ABC...89" in payload_text


def test_low_rms_input_records_diagnostic_without_default_handoff(db):
    session = _voice_session(db)

    result = run_session_turn(
        db,
        session=session,
        audio=_pcm(80, ms=4000),
        worker_id="worker-stt-quality",
        language="en",
        sample_rate=48000,
        channels=1,
        mime_type="audio/pcm",
    )

    contract = _event_payloads(db, session, "webcall_ai.stt.request_contract")[0]
    assert contract["low_input_level"] is True
    assert contract["input_rms_avg"] < 900
    assert result["handoff_required"] is False
    assert _event_payloads(db, session, "webcall_ai.handoff.requested") == []


def test_possible_tts_echo_records_evidence_without_interrupting_session(db):
    FakeDeepgramWebSocket.final_text = "Okay."
    session = _voice_session(db)
    previous = build_handoff_turn(
        db,
        session=session,
        worker_id="worker-stt-quality",
        response_text="Okay.",
        intent="unit_previous_reply",
        handoff_required=False,
        handoff_reason=None,
    )
    write_event(db, conversation_id=session.conversation_id, ticket_id=session.ticket_id, event_type="webcall_ai.response.spoken", payload={"voice_session_id": session.public_id, "turn_id": previous["turn_id"]})
    write_event(db, conversation_id=session.conversation_id, ticket_id=session.ticket_id, event_type="webcall_ai.agent.listening", payload={"voice_session_id": session.public_id})
    db.commit()

    result = run_session_turn(
        db,
        session=session,
        audio=_pcm(1200, ms=4000),
        worker_id="worker-stt-quality",
        language="en",
        sample_rate=48000,
        channels=1,
        mime_type="audio/pcm",
    )

    echo = _event_payloads(db, session, "webcall_ai.stt.possible_tts_echo")[0]
    assert echo["similarity_to_last_ai_response"] >= 0.82
    assert echo["last_ai_spoken_age_ms"] >= 0
    assert echo["listen_started_at"]
    assert echo["tts_finished_at"]
    assert result["handoff_required"] is False
    assert _event_payloads(db, session, "webcall_ai.response.interrupted") == []
