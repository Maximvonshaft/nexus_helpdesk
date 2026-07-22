from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_voice_p0_gap_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.auth_service import create_access_token
from app.db import Base, SessionLocal, engine
from app.enums import UserRole
from app.main import app
from app.models import User
from app.models_agent_routing import ConversationControl
from app.operator_models import OperatorQueueScopeGrant
from app.services.agent_routing_service import set_agent_state
from app.voice_models import VoiceRoutingOffer, WebchatVoiceSession
from app.webchat_models import WebchatConversation, WebchatHandoffRequest


@pytest.fixture(autouse=True)
def isolated_voice_queue(monkeypatch):
    monkeypatch.setenv("WEBCHAT_VOICE_ENABLED", "false")
    monkeypatch.setenv("WEBCHAT_HUMAN_CALL_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_LIVE_AI_VOICE_ENABLED", "false")
    monkeypatch.setenv("WEBCHAT_VOICE_PROVIDER", "mock")
    monkeypatch.setenv("WEBCHAT_VOICE_ALLOWED_PATH_PREFIXES", "/webchat/voice")
    monkeypatch.setenv("WEBCHAT_VOICE_CONNECT_SRC", "wss://voice.example.test")
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        db.add(
            User(
                id=9301,
                username="voice_queue_agent",
                display_name="Voice Queue Agent",
                password_hash="test",
                role=UserRole.admin,
                is_active=True,
            )
        )
        db.commit()
    finally:
        db.close()
    yield
    Base.metadata.drop_all(bind=engine)


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(9301)}"}


def _create_ringing_session(client: TestClient) -> tuple[str, str, str]:
    initialized = client.post(
        "/api/webchat/init",
        json={
            "tenant_key": "pytest-voice-p0",
            "channel_key": "website",
            "visitor_name": "Queue Visitor",
            "page_url": "https://example.test/help",
        },
    )
    assert initialized.status_code == 200, initialized.text
    payload = initialized.json()
    db = SessionLocal()
    try:
        conversation = (
            db.query(WebchatConversation)
            .filter(WebchatConversation.public_id == payload["conversation_id"])
            .one()
        )
        control = (
            db.query(ConversationControl)
            .filter(ConversationControl.conversation_id == conversation.id)
            .one()
        )
        control.country_code = "ME"
        db.add(
            OperatorQueueScopeGrant(
                user_id=9301,
                tenant_key=control.tenant_key,
                country_code="ME",
                channel_key=control.channel_key,
                enabled=True,
                granted_by=9301,
            )
        )
        user = db.get(User, 9301)
        assert user is not None
        set_agent_state(
            db,
            user=user,
            presence_status="online",
            voice_enabled=True,
            max_concurrent_voice_calls=1,
            voice_wrap_up_seconds=0,
        )
        db.commit()
    finally:
        db.close()

    created = client.post(
        f"/api/webchat/conversations/{payload['conversation_id']}/voice/sessions",
        headers={"X-Webchat-Visitor-Token": payload["visitor_token"]},
        json={},
    )
    assert created.status_code == 200, created.text
    return (
        payload["conversation_id"],
        payload["visitor_token"],
        created.json()["voice_session_id"],
    )


def test_agent_ringing_queue_never_exposes_room_credentials():
    client = TestClient(app)
    _conversation_id, _visitor_token, voice_session_id = _create_ringing_session(client)

    response = client.get(
        "/api/webchat/admin/voice/sessions?status=ringing&limit=20",
        headers=_headers(),
    )

    assert response.status_code == 200, response.text
    item = next(
        item
        for item in response.json()["items"]
        if item["voice_session_id"] == voice_session_id
    )
    assert item["status"] == "ringing"
    assert item["visitor_label"] == "Queue Visitor"
    assert item["voice_offer"] is not None
    assert "participant_token" not in item
    assert "participant_identity" not in item


def test_declining_offer_is_idempotent_and_does_not_end_customer_call():
    client = TestClient(app)
    _conversation_id, _visitor_token, voice_session_id = _create_ringing_session(client)

    first = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/reject",
        headers=_headers(),
        json={"reason": "Already handling another call"},
    )
    second = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/reject",
        headers=_headers(),
        json={},
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["status"] == "ringing"
    assert second.json()["status"] == "ringing"
    assert first.json()["participant_token"] is None
    assert first.json()["participant_identity"] is None

    db = SessionLocal()
    try:
        session = (
            db.query(WebchatVoiceSession)
            .filter(WebchatVoiceSession.public_id == voice_session_id)
            .one()
        )
        handoff = db.get(WebchatHandoffRequest, session.handoff_request_id)
        offers = (
            db.query(VoiceRoutingOffer)
            .filter(VoiceRoutingOffer.voice_session_id == session.id)
            .all()
        )
        assert session.status == "ringing"
        assert session.ended_at is None
        assert session.ended_by_user_id is None
        assert handoff is not None and handoff.status == "requested"
        assert handoff.assigned_agent_id is None
        assert len(offers) == 1
        assert offers[0].status == "declined"
    finally:
        db.close()
