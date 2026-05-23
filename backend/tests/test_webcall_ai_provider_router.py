import os
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webcall_ai_provider_router_tests.db")

import pytest

from app import models, operator_models, tool_models, voice_models, webchat_fast_models, webchat_models  # noqa: F401,E402
from app.db import Base, SessionLocal, engine
from app.services.webcall_ai.config import get_webcall_ai_settings
from app.services.webcall_ai.contract_stub_provider import (
    ContractStubSTTProvider,
    ContractStubTTSProvider,
    DisabledSTTProvider,
    DisabledTTSProvider,
)
from app.services.webcall_ai.deepgram_stt_provider import DeepgramSTTProvider
from app.services.webcall_ai.media_schemas import WebCallSTTInput, WebCallTTSInput
from app.services.webcall_ai.mock_media_provider import MockSTTProvider, MockTTSProvider
from app.services.webcall_ai.provider_router import get_stt_provider, get_tts_provider
from app.services.webcall_ai.worker import run_webcall_ai_worker_once
from app.utils.time import utc_now
from app.voice_models import WebchatVoiceAIAction, WebchatVoiceAITurn, WebchatVoiceSession


@pytest.fixture(autouse=True)
def clean_db_and_env(monkeypatch):
    get_webcall_ai_settings.cache_clear()
    monkeypatch.setenv("WEBCALL_AI_AGENT_ENABLED", "true")
    for key in [
        "WEBCALL_STT_PROVIDER",
        "WEBCALL_TTS_PROVIDER",
        "WEBCALL_STT_CONTRACT_STUB_ENABLED",
        "WEBCALL_TTS_CONTRACT_STUB_ENABLED",
        "WEBCALL_STT_DEEPGRAM_ENABLED",
        "WEBCALL_STT_TOKEN",
    ]:
        monkeypatch.delenv(key, raising=False)
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


def _voice_session(db) -> WebchatVoiceSession:
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
    db.commit()
    db.refresh(session)
    return session


def test_default_router_returns_mock_providers():
    assert isinstance(get_stt_provider(), MockSTTProvider)
    assert isinstance(get_tts_provider(), MockTTSProvider)


def test_disabled_router_returns_disabled_providers(monkeypatch):
    monkeypatch.setenv("WEBCALL_STT_PROVIDER", "disabled")
    monkeypatch.setenv("WEBCALL_TTS_PROVIDER", "disabled")
    get_webcall_ai_settings.cache_clear()

    stt = get_stt_provider()
    tts = get_tts_provider()

    assert isinstance(stt, DisabledSTTProvider)
    assert isinstance(tts, DisabledTTSProvider)
    assert stt.transcribe(WebCallSTTInput(voice_session_id=1, worker_id="worker")).status == "disabled"
    assert tts.synthesize(
        WebCallTTSInput(voice_session_id=1, worker_id="worker", text_redacted="hello", language="en")
    ).synthesis_status == "disabled"


def test_contract_stub_router_returns_contract_stub_providers(monkeypatch):
    monkeypatch.setenv("WEBCALL_STT_PROVIDER", "contract_stub")
    monkeypatch.setenv("WEBCALL_TTS_PROVIDER", "contract_stub")
    monkeypatch.setenv("WEBCALL_STT_CONTRACT_STUB_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_TTS_CONTRACT_STUB_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    stt = get_stt_provider()
    tts = get_tts_provider()

    assert isinstance(stt, ContractStubSTTProvider)
    assert isinstance(tts, ContractStubTTSProvider)
    assert stt.transcribe(WebCallSTTInput(voice_session_id=1, worker_id="worker")).error_code
    assert tts.synthesize(
        WebCallTTSInput(voice_session_id=1, worker_id="worker", text_redacted="hello", language="en")
    ).error_code


def test_deepgram_router_returns_deepgram_provider_when_enabled(monkeypatch):
    monkeypatch.setenv("WEBCALL_STT_PROVIDER", "deepgram")
    monkeypatch.setenv("WEBCALL_STT_DEEPGRAM_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_STT_TOKEN", "local-token")
    get_webcall_ai_settings.cache_clear()

    assert isinstance(get_stt_provider(), DeepgramSTTProvider)


def test_disabled_provider_fails_worker_safely_without_turn_or_action(db, monkeypatch):
    session = _voice_session(db)
    monkeypatch.setenv("WEBCALL_STT_PROVIDER", "disabled")
    get_webcall_ai_settings.cache_clear()

    result = run_webcall_ai_worker_once(db, "worker-a", limit=10, lease_seconds=30)
    db.refresh(session)

    assert result["claimed"] == 1
    assert result["failed"] == 1
    assert result["turns"] == 0
    assert result["stt_events"] == 0
    assert result["tts_events"] == 0
    assert session.ai_agent_status == "failed"
    assert session.ai_agent_error_code == "mock_turn_failed"
    assert db.query(WebchatVoiceAITurn).count() == 0
    assert db.query(WebchatVoiceAIAction).count() == 0


def test_contract_stub_provider_fails_worker_safely_without_external_calls(db, monkeypatch):
    session = _voice_session(db)
    monkeypatch.setenv("WEBCALL_STT_PROVIDER", "contract_stub")
    monkeypatch.setenv("WEBCALL_STT_CONTRACT_STUB_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    result = run_webcall_ai_worker_once(db, "worker-a", limit=10, lease_seconds=30)
    db.refresh(session)

    assert result["failed"] == 1
    assert result["turns"] == 0
    assert session.ai_agent_status == "failed"
    assert db.query(WebchatVoiceAITurn).count() == 0
    assert db.query(WebchatVoiceAIAction).count() == 0


@pytest.mark.parametrize(
    ("provider", "enable_key"),
    [
        ("disabled", None),
        ("contract_stub", "WEBCALL_TTS_CONTRACT_STUB_ENABLED"),
    ],
)
def test_tts_unavailable_after_stt_flush_rolls_back_turn_and_action(db, monkeypatch, provider, enable_key):
    session = _voice_session(db)
    monkeypatch.setenv("WEBCALL_TTS_PROVIDER", provider)
    if enable_key:
        monkeypatch.setenv(enable_key, "true")
    get_webcall_ai_settings.cache_clear()

    result = run_webcall_ai_worker_once(db, "worker-a", limit=10, lease_seconds=30)
    db.refresh(session)

    assert result["claimed"] == 1
    assert result["failed"] == 1
    assert result["released"] == 0
    assert result["turns"] == 0
    assert result["stt_events"] == 0
    assert result["tts_events"] == 0
    assert session.ai_agent_status == "failed"
    assert session.ai_agent_error_code == "mock_turn_failed"
    assert db.query(WebchatVoiceAITurn).count() == 0
    assert db.query(WebchatVoiceAIAction).count() == 0


def test_deepgram_worker_without_audio_reference_fails_safely_without_rows(db, monkeypatch):
    session = _voice_session(db)
    monkeypatch.setenv("WEBCALL_STT_PROVIDER", "deepgram")
    monkeypatch.setenv("WEBCALL_STT_DEEPGRAM_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_STT_TOKEN", "local-token")
    get_webcall_ai_settings.cache_clear()

    result = run_webcall_ai_worker_once(db, "worker-a", limit=10, lease_seconds=30)
    db.refresh(session)

    assert result["claimed"] == 1
    assert result["failed"] == 1
    assert result["turns"] == 0
    assert result["stt_events"] == 0
    assert result["tts_events"] == 0
    assert session.ai_agent_status == "failed"
    assert session.ai_agent_error_code == "mock_turn_failed"
    assert db.query(WebchatVoiceAITurn).count() == 0
    assert db.query(WebchatVoiceAIAction).count() == 0
