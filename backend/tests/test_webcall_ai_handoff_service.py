import os
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webcall_ai_handoff_tests.db")

import pytest

from app import models, operator_models, tool_models, voice_models, webchat_fast_models, webchat_models  # noqa: F401,E402
from app.db import Base, SessionLocal, engine
from app.services.webcall_ai.handoff_service import mark_webcall_ai_handoff_required
from app.utils.time import utc_now
from app.voice_models import WebchatVoiceAITurn, WebchatVoiceSession


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _session_turn(db):
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
        customer_text_redacted="Cancel my order and refund me.",
        ai_response_text_redacted="I will connect you to a human support agent.",
        handoff_required=True,
        handoff_reason="high_risk_request",
        created_at=now,
    )
    db.add(turn)
    db.commit()
    db.refresh(session)
    db.refresh(turn)
    return session, turn


def test_mark_handoff_required_creates_safe_idempotent_action(db):
    session, turn = _session_turn(db)

    first = mark_webcall_ai_handoff_required(
        db,
        session=session,
        turn=turn,
        reason="high_risk_request",
        worker_id="worker-a",
    )
    second = mark_webcall_ai_handoff_required(
        db,
        session=session,
        turn=turn,
        reason="high_risk_request",
        worker_id="worker-a",
    )

    assert first.id == second.id
    assert first.model_action == "handoff_to_human"
    assert first.nexus_decision == "handoff"
    assert first.result_status == "handoff_required"
    assert first.speedaf_tool_name is None
    assert first.background_job_id is None
