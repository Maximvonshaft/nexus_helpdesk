import os
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webcall_ai_orchestrator_worker_tests.db")

import pytest

from app import models, operator_models, tool_models, voice_models, webchat_fast_models, webchat_models  # noqa: F401,E402
from app.db import Base, SessionLocal, engine
from app.services.tracking_fact_schema import TrackingFactEvent, TrackingFactResult
from app.services.webcall_ai.config import get_webcall_ai_settings
from app.services.webcall_ai.media_schemas import WebCallSTTInput, WebCallSTTResult
from app.services.webcall_ai.worker import run_webcall_ai_worker_once
from app.utils.time import utc_now
from app.voice_models import WebchatVoiceAIAction, WebchatVoiceAITurn, WebchatVoiceSession


class TextSTTProvider:
    name = "text_stt"

    def __init__(self, text: str):
        self.text = text

    def transcribe(self, input: WebCallSTTInput):
        return WebCallSTTResult(
            text_redacted=self.text,
            language="en",
            confidence=95,
            is_final=True,
            provider=self.name,
            event_count=1,
        )


@pytest.fixture(autouse=True)
def clean_db_and_env(monkeypatch):
    get_webcall_ai_settings.cache_clear()
    monkeypatch.setenv("WEBCALL_AI_AGENT_ENABLED", "true")
    for key in [
        "APP_ENV",
        "WEBCALL_AI_ORCHESTRATOR_ENABLED",
        "WEBCALL_AI_TRACKING_LOOKUP_ENABLED",
        "WEBCALL_AI_TRACKING_REPLY_ENABLED",
        "WEBCALL_AI_TRACKING_COUNTRY_CODE",
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


def test_default_orchestrator_disabled_keeps_existing_mock_response(db):
    _voice_session(db)

    result = run_webcall_ai_worker_once(db, "worker-a", limit=10, lease_seconds=30)
    turn = db.query(WebchatVoiceAITurn).one()
    action = db.query(WebchatVoiceAIAction).one()

    assert result["released"] == 1
    assert turn.ai_response_text_redacted == "Hello, this is Speedaf AI support. Please provide your tracking number."
    assert turn.intent == "tracking_missing_number"
    assert action.result_status == "mock_turn_recorded"


def test_worker_orchestrator_missing_number_writes_ask_tracking_number(db, monkeypatch):
    _voice_session(db)
    monkeypatch.setenv("WEBCALL_AI_ORCHESTRATOR_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_TRACKING_REPLY_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    result = run_webcall_ai_worker_once(db, "worker-a", limit=10, lease_seconds=30)
    turn = db.query(WebchatVoiceAITurn).one()
    action = db.query(WebchatVoiceAIAction).one()

    assert result["released"] == 1
    assert turn.action == "ask_tracking_number"
    assert turn.ai_response_text_redacted == "Please provide your tracking number so I can check the parcel status."
    assert action.model_action == "ask_tracking_number"
    assert action.speedaf_tool_name is None


def test_worker_orchestrator_tracking_lookup_writes_safe_action_evidence(db, monkeypatch):
    _voice_session(db)
    monkeypatch.setenv("WEBCALL_AI_ORCHESTRATOR_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_TRACKING_LOOKUP_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_TRACKING_REPLY_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()
    monkeypatch.setattr(
        "app.services.webcall_ai.mock_turn_executor.get_stt_provider",
        lambda: TextSTTProvider("Track SF123456789CN please"),
    )

    def fake_lookup_tracking_fact(**kwargs):
        return TrackingFactResult(
            ok=True,
            tracking_number=kwargs["tracking_number"],
            status="in_transit",
            status_label="In transit",
            latest_event=TrackingFactEvent(description="Departed facility", location="Zurich"),
            tool_status="success",
            pii_redacted=True,
            fact_evidence_present=True,
        )

    monkeypatch.setattr("app.services.webcall_ai.orchestrator.lookup_tracking_fact", fake_lookup_tracking_fact)

    result = run_webcall_ai_worker_once(db, "worker-a", limit=10, lease_seconds=30)
    turn = db.query(WebchatVoiceAITurn).one()
    action = db.query(WebchatVoiceAIAction).one()

    assert result["released"] == 1
    assert turn.action == "explain_tracking_fact"
    assert turn.intent == "tracking_status_lookup"
    assert turn.tracking_number_hash and turn.tracking_number_hash.startswith("sha256:")
    assert "SF123456789CN" not in (turn.tracking_number_hash or "")
    assert "SF123456789CN" not in (turn.ai_response_text_redacted or "")
    assert action.speedaf_tool_name == "speedaf.order.query"
    assert action.result_status == "tracking_fact_explained"
    assert "SF123456789CN" not in (action.decision_reason or "")
    assert "SF123456789CN" not in (action.result_status or "")


def test_worker_tracking_reply_disabled_handoffs_without_lookup(db, monkeypatch):
    _voice_session(db)
    monkeypatch.setenv("WEBCALL_AI_ORCHESTRATOR_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_TRACKING_LOOKUP_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_TRACKING_REPLY_ENABLED", "false")
    get_webcall_ai_settings.cache_clear()
    monkeypatch.setattr(
        "app.services.webcall_ai.mock_turn_executor.get_stt_provider",
        lambda: TextSTTProvider("Track SF123456789CN please"),
    )
    monkeypatch.setattr(
        "app.services.webcall_ai.orchestrator.lookup_tracking_fact",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("lookup should not be called")),
    )

    run_webcall_ai_worker_once(db, "worker-a", limit=10, lease_seconds=30)
    turn = db.query(WebchatVoiceAITurn).one()
    action = db.query(WebchatVoiceAIAction).one()

    assert turn.action == "handoff_to_human"
    assert turn.intent == "tracking_reply_disabled"
    assert turn.handoff_required is True
    assert action.nexus_decision == "handoff"
    assert action.speedaf_tool_name is None
    assert action.result_status == "tracking_reply_disabled"


def test_worker_orchestrator_forbidden_request_handoffs_without_lookup(db, monkeypatch):
    _voice_session(db)
    monkeypatch.setenv("WEBCALL_AI_ORCHESTRATOR_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_TRACKING_LOOKUP_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_TRACKING_REPLY_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()
    monkeypatch.setattr(
        "app.services.webcall_ai.mock_turn_executor.get_stt_provider",
        lambda: TextSTTProvider("Cancel order SF123456789CN"),
    )
    monkeypatch.setattr(
        "app.services.webcall_ai.orchestrator.lookup_tracking_fact",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("lookup should not be called")),
    )

    run_webcall_ai_worker_once(db, "worker-a", limit=10, lease_seconds=30)
    turn = db.query(WebchatVoiceAITurn).one()
    action = db.query(WebchatVoiceAIAction).one()

    assert turn.action == "handoff_to_human"
    assert turn.handoff_required is True
    assert action.nexus_decision == "handoff"
    assert action.speedaf_tool_name is None
