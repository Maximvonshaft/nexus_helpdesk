import os
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webcall_ai_tts_runtime_tests.db")

import pytest

from app import models, operator_models, tool_models, voice_models, webchat_fast_models, webchat_models  # noqa: F401,E402
from app.db import Base, SessionLocal, engine
from app.services.webcall_ai.config import get_webcall_ai_settings
from app.services.webcall_ai.media_schemas import WebCallTTSInput, WebCallTTSResult
from app.services.webcall_ai.tts_runtime import run_tts_runtime_for_turn
from app.utils.time import utc_now
from app.voice_models import WebchatVoiceAITurn, WebchatVoiceSession


class FakeTTSProvider:
    name = "fake_tts"

    def __init__(self, result: WebCallTTSResult | None = None):
        self.result = result
        self.inputs: list[WebCallTTSInput] = []

    def synthesize(self, input: WebCallTTSInput):
        self.inputs.append(input)
        return self.result or WebCallTTSResult(
            provider=self.name,
            voice=input.voice,
            language=input.language,
            text_redacted=input.text_redacted,
            synthesis_status="ok",
            audio_reference="mock://tts/runtime-audio",
            event_count=1,
        )


@pytest.fixture(autouse=True)
def clean_db_and_env(monkeypatch):
    get_webcall_ai_settings.cache_clear()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    get_webcall_ai_settings.cache_clear()


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _session_and_turn(db, *, reply: str | None = "Please provide your tracking number."):
    now = utc_now()
    session = WebchatVoiceSession(
        public_id=f"voice_{uuid4().hex}",
        conversation_id=1,
        ticket_id=1,
        provider="livekit",
        provider_room_name=f"room_{uuid4().hex}",
        status="ringing",
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.flush()
    turn = WebchatVoiceAITurn(
        voice_session_id=session.id,
        conversation_id=1,
        ticket_id=1,
        turn_index=1,
        customer_text_redacted="Track my parcel.",
        ai_response_text_redacted=reply,
        language="en",
        intent="tracking_missing_number",
        action="ask_tracking_number",
        handoff_required=False,
        confidence=100,
        provider="mock",
        stt_provider="mock",
        tts_provider="mock",
        latency_ms=0,
        created_at=now,
    )
    db.add(turn)
    db.commit()
    db.refresh(session)
    db.refresh(turn)
    return session, turn


def test_mock_tts_runtime_returns_usable_audio_reference(db):
    session, turn = _session_and_turn(db)

    result = run_tts_runtime_for_turn(turn=turn, session=session, worker_id="worker-a")

    assert result.usable is True
    assert result.provider == "mock"
    assert result.audio_reference == "mock://tts/webcall-ai-support-greeting"
    assert result.tts_events == 1


def test_tts_runtime_uses_provider_and_input_contract(db, monkeypatch):
    session, turn = _session_and_turn(db)
    provider = FakeTTSProvider()
    monkeypatch.setattr("app.services.webcall_ai.tts_runtime.get_tts_provider", lambda settings: provider)

    result = run_tts_runtime_for_turn(turn=turn, session=session, worker_id="worker-a")

    assert result.usable is True
    assert provider.inputs[0].text_redacted == "Please provide your tracking number."
    assert provider.inputs[0].language == "en"


def test_disabled_or_contract_stub_tts_returns_unusable_safe_result(db, monkeypatch):
    session, turn = _session_and_turn(db)
    provider = FakeTTSProvider(
        WebCallTTSResult(
            provider="disabled",
            voice="mock_support_voice",
            language="en",
            text_redacted=turn.ai_response_text_redacted or "",
            synthesis_status="disabled",
            audio_reference=None,
            event_count=0,
            error_code="tts_provider_disabled",
        )
    )
    monkeypatch.setattr("app.services.webcall_ai.tts_runtime.get_tts_provider", lambda settings: provider)

    result = run_tts_runtime_for_turn(turn=turn, session=session, worker_id="worker-a")

    assert result.usable is False
    assert result.error_code == "tts_provider_disabled"
    assert result.audio_reference is None


def test_missing_ai_reply_text_returns_unusable_without_provider_call(db, monkeypatch):
    session, turn = _session_and_turn(db, reply=None)
    monkeypatch.setattr(
        "app.services.webcall_ai.tts_runtime.get_tts_provider",
        lambda settings: (_ for _ in ()).throw(AssertionError("provider should not be called")),
    )

    result = run_tts_runtime_for_turn(turn=turn, session=session, worker_id="worker-a")

    assert result.usable is False
    assert result.error_code == "tts_reply_text_required"
    assert result.audio_reference is None
