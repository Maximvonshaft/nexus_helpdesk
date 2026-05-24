from __future__ import annotations

import os
from uuid import uuid4

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webcall_ai_voice_loop_tests.db")

import pytest

from app import models, operator_models, tool_models, voice_models, webchat_fast_models, webchat_models  # noqa: F401,E402
from app.db import Base, SessionLocal, engine
from app.services.webcall_ai_production.agent_session_claims import AI_STATUS_CLAIMED
from app.services.webcall_ai_production.agent_worker import run_claimed_session_loop
from app.services.webcall_ai_production.audio.livekit_io import LiveKitMediaTurn, decode_audio_for_livekit
from app.services.webcall_ai_production.config import get_webcall_ai_production_settings
from app.services.webcall_ai_production.providers.external_llm import ExternalLLMProvider
from app.services.webcall_ai_production.providers.external_stt import ExternalSTTProvider
from app.services.webcall_ai_production.providers.external_tts import ExternalTTSProvider
from app.services.webcall_ai_production.tools.tracking_lookup import lookup_tracking
from app.utils.time import utc_now
from app.voice_models import WebchatVoiceAIAction, WebchatVoiceAITurn, WebchatVoiceSession, WebchatVoiceTranscriptSegment
from app.webchat_models import WebchatEvent


@pytest.fixture(autouse=True)
def clean_db_and_env(monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_PRODUCTION_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_AGENT_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_PROVIDER_PROFILE", "fake")
    monkeypatch.setenv("WEBCALL_AI_MAX_SESSION_SECONDS", "60")
    monkeypatch.setenv("WEBCALL_AI_MAX_TURNS_PER_SESSION", "5")
    monkeypatch.setenv("WEBCHAT_VOICE_PROVIDER", "mock")
    monkeypatch.setenv("WEBCHAT_VOICE_ENABLED", "true")
    monkeypatch.setenv("STT_PROVIDER", "fake")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("TTS_PROVIDER", "fake")
    get_webcall_ai_production_settings.cache_clear()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    get_webcall_ai_production_settings.cache_clear()


class FakeAgentIO:
    def __init__(self, utterances: list[bytes]):
        self.utterances = list(utterances)
        self.published: list[tuple[bytes, str]] = []
        self.connected = False
        self.closed = False

    def connect(self):
        self.connected = True

    def collect_next_customer_utterance(self, *, timeout_seconds=20.0, max_seconds=12.0):
        if not self.utterances:
            raise RuntimeError("no more utterances")
        return LiveKitMediaTurn(customer_audio=self.utterances.pop(0), language="en")

    def publish_ai_audio(self, audio_bytes: bytes, *, mime_type: str):
        self.published.append((audio_bytes, mime_type))

    def close(self):
        self.closed = True


def _claimed_session(db) -> WebchatVoiceSession:
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
        ai_agent_claimed_at=utc_now(),
        ai_agent_last_heartbeat_at=utc_now(),
        ai_agent_lease_expires_at=utc_now(),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def test_agent_loop_writes_redacted_evidence_and_handoff_for_unconfigured_tracking():
    db = SessionLocal()
    try:
        session = _claimed_session(db)
        io = FakeAgentIO([b"Please track SF123456789CN"])

        result = run_claimed_session_loop(session.id, worker_id="worker-test", io=io)

        assert result["status"] == "handoff_required"
        assert io.connected is True
        assert io.closed is True
        assert len(io.published) >= 2
        assert db.query(WebchatVoiceTranscriptSegment).count() >= 1
        assert db.query(WebchatVoiceAITurn).count() >= 2
        assert db.query(WebchatVoiceAIAction).count() >= 2
        payloads = [event.event_type for event in db.query(WebchatEvent).order_by(WebchatEvent.id).all()]
        assert "webcall_ai.agent.joined" in payloads
        assert "webcall_ai.transcript.final" in payloads
        assert "webcall_ai.tool.called" in payloads
        assert "webcall_ai.handoff.requested" in payloads
        transcript = db.query(WebchatVoiceTranscriptSegment).filter(WebchatVoiceTranscriptSegment.text_redacted.like("%SF1%")).first()
        assert transcript is not None
        assert "SF123456789CN" not in transcript.text_redacted
    finally:
        db.close()


def test_tracking_lookup_boundary_is_fail_closed_without_provider_config():
    result = lookup_tracking({"tracking_number": "SF123456789CN"})

    assert result["status"] == "not_configured"
    assert result["tracking_number_redacted"] == "SF1...CN"


def test_livekit_audio_decoder_accepts_pcm_bytes(monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_TTS_SAMPLE_RATE", "24000")
    pcm, sample_rate, channels = decode_audio_for_livekit(b"\x00\x00" * 240, mime_type="audio/pcm")

    assert len(pcm) == 480
    assert sample_rate == 24000
    assert channels == 1


def test_external_provider_adapters_require_secret_files(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")

    with pytest.raises(Exception, match="secret file"):
        ExternalSTTProvider(endpoint="https://stt.example.test", token_file="").transcribe(b"audio")
    with pytest.raises(Exception, match="secret file"):
        ExternalLLMProvider(endpoint="https://llm.example.test", token_file="").respond("track package")
    with pytest.raises(Exception, match="secret file"):
        ExternalTTSProvider(endpoint="https://tts.example.test", token_file="").synthesize("hello")


def test_external_provider_adapters_parse_mocked_http(monkeypatch, tmp_path):
    secret = tmp_path / "provider-token"
    secret.write_text("token-value", encoding="utf-8")

    class Response:
        headers = {"content-type": "audio/wav"}
        content = b"RIFFaudio"

        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class Client:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, endpoint, **kwargs):
            if "stt" in endpoint:
                return Response({"text": "track SF123456789CN", "language": "en", "confidence": 91})
            if "llm" in endpoint:
                return Response({"response_text": "Please hold while I check.", "intent": "tracking_lookup", "handoff_required": False})
            return Response({})

    monkeypatch.setattr("httpx.Client", Client)

    stt = ExternalSTTProvider(endpoint="https://stt.example.test", token_file=str(secret)).transcribe(b"audio")
    llm = ExternalLLMProvider(endpoint="https://llm.example.test", token_file=str(secret)).respond(stt.text)
    tts = ExternalTTSProvider(endpoint="https://tts.example.test", token_file=str(secret)).synthesize(llm.response_text)

    assert stt.text == "track SF123456789CN"
    assert llm.intent == "tracking_lookup"
    assert tts.audio_bytes == b"RIFFaudio"
