from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_voice_api_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.auth_service import create_access_token
from app.db import Base, SessionLocal, engine
from app.enums import UserRole
from app.main import app
from app.models import Ticket, User
from app.models_agent_routing import ConversationControl
from app.operator_models import OperatorQueueScopeGrant
from app.services import webchat_rate_limit as webchat_rate_limit_service
from app.services.agent_routing_service import set_agent_state
from app.utils.time import utc_now
from app.voice_models import (
    VoiceRoutingOffer,
    WebchatVoiceSession,
    WebchatVoiceSessionAction,
)
from app.webchat_models import WebchatConversation, WebchatHandoffRequest


@pytest.fixture(autouse=True)
def canonical_voice_database(monkeypatch):
    monkeypatch.setenv("WEBCHAT_VOICE_ENABLED", "false")
    monkeypatch.setenv("WEBCHAT_HUMAN_CALL_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_LIVE_AI_VOICE_ENABLED", "false")
    monkeypatch.setenv("WEBCHAT_VOICE_PROVIDER", "mock")
    monkeypatch.setenv("WEBCHAT_VOICE_ALLOWED_PATH_PREFIXES", "/webchat/voice")
    monkeypatch.setenv("WEBCHAT_VOICE_CONNECT_SRC", "wss://voice.example.test")
    monkeypatch.setattr(
        webchat_rate_limit_service.settings,
        "webchat_rate_limit_max_requests",
        1000,
    )
    webchat_rate_limit_service._MEMORY_BUCKETS.clear()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        for user_id, username in (
            (9201, "voice_admin"),
            (9202, "voice_agent_a"),
            (9203, "voice_agent_b"),
        ):
            db.add(
                User(
                    id=user_id,
                    username=username,
                    display_name=username.replace("_", " ").title(),
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


def _headers(user_id: int) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def _init_conversation(
    client: TestClient,
    *,
    country_code: str = "ME",
    agent_ids: tuple[int, ...] = (9202,),
) -> tuple[str, str]:
    response = client.post(
        "/api/webchat/init",
        json={
            "tenant_key": "pytest-voice",
            "channel_key": "website",
            "visitor_name": "Voice Visitor",
            "page_url": "https://example.test/help",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
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
        control.country_code = country_code
        for user_id in agent_ids:
            db.add(
                OperatorQueueScopeGrant(
                    user_id=user_id,
                    tenant_key=control.tenant_key,
                    country_code=country_code,
                    channel_key=control.channel_key,
                    enabled=True,
                    granted_by=9201,
                )
            )
            user = db.get(User, user_id)
            assert user is not None
            set_agent_state(
                db,
                user=user,
                presence_status="online",
                max_concurrent_conversations=3,
                voice_enabled=True,
                max_concurrent_voice_calls=1,
                voice_wrap_up_seconds=0,
            )
        db.commit()
    finally:
        db.close()
    return payload["conversation_id"], payload["visitor_token"]


def _create_session(
    client: TestClient,
    *,
    agent_ids: tuple[int, ...] = (9202,),
) -> tuple[str, str, dict]:
    conversation_id, visitor_token = _init_conversation(
        client,
        agent_ids=agent_ids,
    )
    response = client.post(
        f"/api/webchat/conversations/{conversation_id}/voice/sessions",
        headers={"X-Webchat-Visitor-Token": visitor_token},
        json={"locale": "en"},
    )
    assert response.status_code == 200, response.text
    return conversation_id, visitor_token, response.json()


def test_runtime_config_exposes_livekit_url_without_credentials(monkeypatch):
    monkeypatch.setenv("WEBCHAT_VOICE_PROVIDER", "livekit")
    monkeypatch.setenv("LIVEKIT_URL", "wss://voice.example.test")
    monkeypatch.setenv("LIVEKIT_API_KEY", "unit_key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "unit_secret")
    monkeypatch.setenv(
        "WEBCHAT_VOICE_CONNECT_SRC",
        "wss://voice.example.test https://voice.example.test",
    )

    response = TestClient(app).get("/api/webchat/voice/runtime-config")

    assert response.status_code == 200, response.text
    assert response.json()["media_plane"] == "livekit"
    assert response.json()["livekit_url"] == "wss://voice.example.test"
    assert "unit_key" not in response.text
    assert "unit_secret" not in response.text


def test_ticketless_call_creates_handoff_and_offer_without_ticket():
    client = TestClient(app)
    conversation_id, _visitor_token, payload = _create_session(client)

    assert payload["provider"] == "mock"
    assert payload["status"] == "ringing"
    assert payload["voice_offer"] is not None
    assert payload["participant_token"].startswith("mock_voice_token_")

    db = SessionLocal()
    try:
        conversation = (
            db.query(WebchatConversation)
            .filter(WebchatConversation.public_id == conversation_id)
            .one()
        )
        session = (
            db.query(WebchatVoiceSession)
            .filter(WebchatVoiceSession.public_id == payload["voice_session_id"])
            .one()
        )
        handoff = db.get(WebchatHandoffRequest, session.handoff_request_id)
        offer = (
            db.query(VoiceRoutingOffer)
            .filter(VoiceRoutingOffer.voice_session_id == session.id)
            .one()
        )
        assert conversation.ticket_id is None
        assert session.ticket_id is None
        assert handoff is not None and handoff.status == "requested"
        assert handoff.assigned_agent_id is None
        assert conversation.active_agent_id is None
        assert offer.status == "offered"
        assert offer.agent_id == 9202
        assert db.query(Ticket).count() == 0
    finally:
        db.close()


def test_missing_country_scope_fails_before_room_creation():
    client = TestClient(app)
    response = client.post(
        "/api/webchat/init",
        json={
            "tenant_key": "pytest-voice",
            "channel_key": "website",
            "visitor_name": "Unscoped Visitor",
        },
    )
    payload = response.json()

    created = client.post(
        f"/api/webchat/conversations/{payload['conversation_id']}/voice/sessions",
        headers={"X-Webchat-Visitor-Token": payload["visitor_token"]},
        json={},
    )

    assert created.status_code == 409
    assert created.json()["detail"] == "conversation_scope_unavailable"
    assert SessionLocal().query(WebchatVoiceSession).count() == 0


def test_only_offered_agent_can_accept_and_ownership_lands_on_handoff():
    client = TestClient(app)
    conversation_id, _token, payload = _create_session(
        client,
        agent_ids=(9202, 9203),
    )
    voice_session_id = payload["voice_session_id"]
    db = SessionLocal()
    try:
        session = (
            db.query(WebchatVoiceSession)
            .filter(WebchatVoiceSession.public_id == voice_session_id)
            .one()
        )
        offer = (
            db.query(VoiceRoutingOffer)
            .filter(
                VoiceRoutingOffer.voice_session_id == session.id,
                VoiceRoutingOffer.status == "offered",
            )
            .one()
        )
        offered_agent_id = offer.agent_id
        other_agent_id = 9203 if offered_agent_id == 9202 else 9202
    finally:
        db.close()

    wrong = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/accept",
        headers=_headers(other_agent_id),
    )
    assert wrong.status_code == 409
    assert "participant_token" not in wrong.text

    accepted = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/accept",
        headers=_headers(offered_agent_id),
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "active"
    assert accepted.json()["accepted_by_user_id"] == offered_agent_id

    db = SessionLocal()
    try:
        conversation = (
            db.query(WebchatConversation)
            .filter(WebchatConversation.public_id == conversation_id)
            .one()
        )
        session = (
            db.query(WebchatVoiceSession)
            .filter(WebchatVoiceSession.public_id == voice_session_id)
            .one()
        )
        handoff = db.get(WebchatHandoffRequest, session.handoff_request_id)
        assert handoff is not None and handoff.status == "accepted"
        assert handoff.assigned_agent_id == offered_agent_id
        assert conversation.active_agent_id == offered_agent_id
        assert not hasattr(session, "accepted_by_user_id")
    finally:
        db.close()


def test_decline_releases_offer_and_keeps_customer_call_alive():
    client = TestClient(app)
    _conversation_id, _token, payload = _create_session(
        client,
        agent_ids=(9202, 9203),
    )
    voice_session_id = payload["voice_session_id"]
    db = SessionLocal()
    try:
        session = (
            db.query(WebchatVoiceSession)
            .filter(WebchatVoiceSession.public_id == voice_session_id)
            .one()
        )
        first_offer = (
            db.query(VoiceRoutingOffer)
            .filter(
                VoiceRoutingOffer.voice_session_id == session.id,
                VoiceRoutingOffer.status == "offered",
            )
            .one()
        )
        first_agent_id = first_offer.agent_id
    finally:
        db.close()

    rejected = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/reject",
        headers=_headers(first_agent_id),
        json={"reason": "Already on another call"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "ringing"

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
            .order_by(VoiceRoutingOffer.sequence.asc())
            .all()
        )
        assert session.status == "ringing"
        assert session.ended_at is None
        assert handoff is not None and handoff.status == "requested"
        assert handoff.assigned_agent_id is None
        assert offers[0].status == "declined"
        assert offers[-1].status == "offered"
        assert offers[-1].agent_id != first_agent_id
    finally:
        db.close()


def test_operator_call_control_creates_durable_command_only():
    client = TestClient(app)
    _conversation_id, _token, payload = _create_session(client)
    voice_session_id = payload["voice_session_id"]
    accepted = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/accept",
        headers=_headers(9202),
    )
    assert accepted.status_code == 200, accepted.text

    command = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/actions",
        headers=_headers(9202),
        json={
            "action_type": "keypad",
            "digits": "123#",
            "note": "Menu selection",
        },
    )
    assert command.status_code == 200, command.text
    action = command.json()["action"]
    assert action["status"] == "requested"
    assert action["provider_status"] == "pending"
    assert "123#" not in command.text

    db = SessionLocal()
    try:
        row = (
            db.query(WebchatVoiceSessionAction)
            .filter(WebchatVoiceSessionAction.public_id == action["id"])
            .one()
        )
        assert row.action_type == "keypad"
        assert row.attempt_count == 0
        assert "123#" not in (row.result_json or "")
    finally:
        db.close()


def test_customer_hangup_closes_call_and_requested_handoff_without_ticket():
    client = TestClient(app)
    conversation_id, visitor_token, payload = _create_session(client)
    voice_session_id = payload["voice_session_id"]

    ended = client.post(
        f"/api/webchat/conversations/{conversation_id}/voice/{voice_session_id}/end",
        headers={"X-Webchat-Visitor-Token": visitor_token},
    )
    assert ended.status_code == 200, ended.text
    assert ended.json()["status"] == "cancelled"

    db = SessionLocal()
    try:
        conversation = (
            db.query(WebchatConversation)
            .filter(WebchatConversation.public_id == conversation_id)
            .one()
        )
        session = (
            db.query(WebchatVoiceSession)
            .filter(WebchatVoiceSession.public_id == voice_session_id)
            .one()
        )
        handoff = db.get(WebchatHandoffRequest, session.handoff_request_id)
        assert session.ended_at is not None
        assert handoff is not None and handoff.status == "closed"
        assert conversation.active_agent_id is None
        assert conversation.ticket_id is None
    finally:
        db.close()


def test_invalid_visitor_token_is_rejected():
    client = TestClient(app)
    conversation_id, _token = _init_conversation(client)

    response = client.post(
        f"/api/webchat/conversations/{conversation_id}/voice/sessions",
        headers={"X-Webchat-Visitor-Token": "invalid"},
        json={},
    )

    assert response.status_code == 403
