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
from app.models import User
from app.services import webchat_voice_service
from app.services.livekit_voice_provider import LiveKitVoiceProvider
from app.services.voice_provider import VoiceParticipantToken
from app.voice_models import WebchatVoiceSession
from app.webchat_models import WebchatEvent, WebchatMessage  # noqa: F401 - ensure metadata registration


@pytest.fixture(scope="module", autouse=True)
def ensure_schema():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        users = [
            User(id=9201, username="voice_admin", display_name="Voice Admin", password_hash="test", role=UserRole.admin, is_active=True),
            User(id=9202, username="voice_admin_a", display_name="Voice Admin A", password_hash="test", role=UserRole.admin, is_active=True),
            User(id=9203, username="voice_admin_b", display_name="Voice Admin B", password_hash="test", role=UserRole.admin, is_active=True),
            User(id=9204, username="voice_outsider", display_name="Voice Outsider", password_hash="test", role=UserRole.agent, is_active=True),
        ]
        for user in users:
            existing = db.query(User).filter(User.id == user.id).first()
            if existing is None:
                db.add(user)
            else:
                existing.username = user.username
                existing.display_name = user.display_name
                existing.role = user.role
                existing.is_active = True
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def voice_env(monkeypatch):
    monkeypatch.setenv("WEBCHAT_VOICE_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_VOICE_PROVIDER", "mock")
    monkeypatch.setenv("WEBCHAT_VOICE_ALLOWED_PATH_PREFIXES", "/webchat/voice")
    monkeypatch.setenv("WEBCHAT_VOICE_CONNECT_SRC", "wss://voice.example.test")
    yield


def _admin_headers(user_id: int = 9201) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def _create_webchat_conversation(client: TestClient, name: str = "Voice Visitor") -> tuple[str, str, int]:
    init = client.post(
        "/api/webchat/init",
        json={
            "tenant_key": "pytest-voice",
            "channel_key": "website",
            "visitor_name": name,
            "page_url": "https://example.test/help",
        },
    )
    assert init.status_code == 200, init.text
    payload = init.json()
    conversation_id = payload["conversation_id"]
    visitor_token = payload["visitor_token"]

    thread_candidates = client.get("/api/webchat/admin/conversations", headers=_admin_headers())
    assert thread_candidates.status_code == 200, thread_candidates.text
    ticket_id = next(item["ticket_id"] for item in thread_candidates.json() if item["conversation_id"] == conversation_id)
    return conversation_id, visitor_token, ticket_id


def test_voice_runtime_config_exposes_livekit_url_without_secrets(monkeypatch):
    monkeypatch.setenv("WEBCHAT_VOICE_PROVIDER", "livekit")
    monkeypatch.setenv("LIVEKIT_URL", "wss://voice.example.test")
    monkeypatch.setenv("LIVEKIT_API_KEY", "unit_key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "unit_secret")
    monkeypatch.setenv("WEBCHAT_VOICE_CONNECT_SRC", "wss://voice.example.test https://voice.example.test")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/api/webchat/voice/runtime-config")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["enabled"] is True
    assert payload["provider"] == "livekit"
    assert payload["livekit_url"] == "wss://voice.example.test"
    assert "unit_secret" not in response.text
    assert "LIVEKIT_API_SECRET" not in response.text
    assert "unit_key" not in response.text


def test_public_create_voice_session_binds_conversation_and_ticket():
    client = TestClient(app, raise_server_exceptions=False)
    conversation_id, visitor_token, ticket_id = _create_webchat_conversation(client)

    created = client.post(
        f"/api/webchat/conversations/{conversation_id}/voice/sessions",
        headers={"X-Webchat-Visitor-Token": visitor_token},
        json={"locale": "de-CH", "recording_consent": False},
    )

    assert created.status_code == 200, created.text
    payload = created.json()
    assert payload["ok"] is True
    assert payload["voice_session_id"].startswith("wv_")
    assert payload["provider"] == "mock"
    assert payload["status"] == "ringing"
    assert payload["voice_page_url"].endswith(payload["voice_session_id"])
    assert payload["participant_token"].startswith("mock_voice_token_")
    assert "ticket_id" not in payload

    db = SessionLocal()
    try:
        row = db.query(WebchatVoiceSession).filter(WebchatVoiceSession.public_id == payload["voice_session_id"]).one()
        assert row.ticket_id == ticket_id
        assert row.provider == "mock"
        events = db.query(WebchatEvent).filter(WebchatEvent.ticket_id == ticket_id).all()
        event_types = {event.event_type for event in events}
        assert "voice.session.created" in event_types
        assert "voice.session.ringing" in event_types
    finally:
        db.close()


def test_public_create_voice_session_rejects_invalid_token():
    client = TestClient(app, raise_server_exceptions=False)
    conversation_id, _visitor_token, _ticket_id = _create_webchat_conversation(client)

    created = client.post(
        f"/api/webchat/conversations/{conversation_id}/voice/sessions",
        headers={"X-Webchat-Visitor-Token": "invalid-token"},
        json={},
    )

    assert created.status_code == 403


def test_public_create_voice_session_returns_existing_active_session():
    client = TestClient(app, raise_server_exceptions=False)
    conversation_id, visitor_token, _ticket_id = _create_webchat_conversation(client)

    first = client.post(
        f"/api/webchat/conversations/{conversation_id}/voice/sessions",
        headers={"X-Webchat-Visitor-Token": visitor_token},
        json={},
    )
    second = client.post(
        f"/api/webchat/conversations/{conversation_id}/voice/sessions",
        headers={"X-Webchat-Visitor-Token": visitor_token},
        json={},
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["voice_session_id"] == second.json()["voice_session_id"]


def test_admin_accept_first_agent_wins_and_end_writes_single_final_message():
    client = TestClient(app)
    conversation_id, visitor_token, ticket_id = _create_webchat_conversation(client)
    created = client.post(
        f"/api/webchat/conversations/{conversation_id}/voice/sessions",
        headers={"X-Webchat-Visitor-Token": visitor_token},
        json={},
    )
    assert created.status_code == 200, created.text
    voice_session_id = created.json()["voice_session_id"]

    accepted = client.post(
        f"/api/webchat/admin/tickets/{ticket_id}/voice/{voice_session_id}/accept",
        headers=_admin_headers(9202),
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "active"
    assert accepted.json()["accepted_by_user_id"] == 9202

    second_accept = client.post(
        f"/api/webchat/admin/tickets/{ticket_id}/voice/{voice_session_id}/accept",
        headers=_admin_headers(9203),
    )
    assert second_accept.status_code == 409

    ended = client.post(
        f"/api/webchat/admin/tickets/{ticket_id}/voice/{voice_session_id}/end",
        headers=_admin_headers(9202),
    )
    assert ended.status_code == 200, ended.text
    ended_again = client.post(
        f"/api/webchat/admin/tickets/{ticket_id}/voice/{voice_session_id}/end",
        headers=_admin_headers(9202),
    )
    assert ended_again.status_code == 200, ended_again.text

    db = SessionLocal()
    try:
        final_messages = db.query(WebchatMessage).filter(WebchatMessage.ticket_id == ticket_id, WebchatMessage.message_type == "voice_call").all()
        assert len(final_messages) == 1
        assert final_messages[0].client_message_id == f"voice-call-ended:{voice_session_id}"
        events = db.query(WebchatEvent).filter(WebchatEvent.ticket_id == ticket_id).all()
        event_types = [event.event_type for event in events]
        assert "voice.session.accepted" in event_types
        assert "voice.session.active" in event_types
        assert "voice.session.ended" in event_types
    finally:
        db.close()


def test_livekit_provider_create_accept_end_without_external_api(monkeypatch):
    created_rooms: list[str] = []
    closed_rooms: list[str] = []

    def fake_create_room(self, *, room_name: str) -> str:
        created_rooms.append(room_name)
        return room_name

    def fake_close_room(self, *, room_name: str) -> None:
        closed_rooms.append(room_name)
        return None

    def fake_issue_token(self, *, room_name: str, participant_identity: str, ttl_seconds: int) -> VoiceParticipantToken:
        return VoiceParticipantToken(
            provider="livekit",
            room_name=room_name,
            participant_identity=participant_identity,
            participant_token=f"fake_livekit_token::{participant_identity}::{room_name}",
            expires_in_seconds=ttl_seconds,
        )

    monkeypatch.setenv("WEBCHAT_VOICE_PROVIDER", "livekit")
    monkeypatch.setenv("LIVEKIT_URL", "wss://voice.example.test")
    monkeypatch.setenv("LIVEKIT_API_KEY", "unit_key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "unit_secret")
    monkeypatch.setenv("WEBCHAT_VOICE_CONNECT_SRC", "wss://voice.example.test https://voice.example.test")
    monkeypatch.setattr(LiveKitVoiceProvider, "create_room", fake_create_room)
    monkeypatch.setattr(LiveKitVoiceProvider, "close_room", fake_close_room)
    monkeypatch.setattr(LiveKitVoiceProvider, "issue_participant_token", fake_issue_token)

    client = TestClient(app)
    conversation_id, visitor_token, ticket_id = _create_webchat_conversation(client, name="LiveKit Visitor")

    created = client.post(
        f"/api/webchat/conversations/{conversation_id}/voice/sessions",
        headers={"X-Webchat-Visitor-Token": visitor_token},
        json={},
    )
    assert created.status_code == 200, created.text
    payload = created.json()
    voice_session_id = payload["voice_session_id"]
    assert payload["provider"] == "livekit"
    assert payload["status"] == "ringing"
    assert payload["provider_room_name"].startswith("webcall_wv_")
    assert payload["room_name"] == payload["provider_room_name"]
    assert payload["participant_identity"].startswith("visitor_")
    assert payload["participant_token"].startswith("fake_livekit_token::visitor_")
    assert "unit_secret" not in payload["participant_token"]
    assert created_rooms == [payload["provider_room_name"]]

    accepted = client.post(
        f"/api/webchat/admin/tickets/{ticket_id}/voice/{voice_session_id}/accept",
        headers=_admin_headers(9202),
    )
    assert accepted.status_code == 200, accepted.text
    accepted_payload = accepted.json()
    assert accepted_payload["status"] == "active"
    assert accepted_payload["provider"] == "livekit"
    assert accepted_payload["participant_identity"].startswith("agent_")
    assert accepted_payload["participant_token"].startswith("fake_livekit_token::agent_")

    ended = client.post(
        f"/api/webchat/admin/tickets/{ticket_id}/voice/{voice_session_id}/end",
        headers=_admin_headers(9202),
    )
    assert ended.status_code == 200, ended.text
    assert closed_rooms == [payload["provider_room_name"]]

    db = SessionLocal()
    try:
        row = db.query(WebchatVoiceSession).filter(WebchatVoiceSession.public_id == voice_session_id).one()
        assert row.provider == "livekit"
        assert row.provider_room_name == payload["provider_room_name"]
        assert row.ticket_id == ticket_id
        final_message = db.query(WebchatMessage).filter(WebchatMessage.ticket_id == ticket_id, WebchatMessage.message_type == "voice_call").order_by(WebchatMessage.id.desc()).first()
        assert final_message is not None
        assert final_message.client_message_id == f"voice-call-ended:{voice_session_id}"
        event_types = [event.event_type for event in db.query(WebchatEvent).filter(WebchatEvent.ticket_id == ticket_id).all()]
        assert "voice.session.created" in event_types
        assert "voice.session.ringing" in event_types
        assert "voice.session.accepted" in event_types
        assert "voice.session.active" in event_types
        assert "voice.session.ended" in event_types
    finally:
        db.close()


def test_admin_voice_endpoint_requires_ticket_visibility():
    client = TestClient(app)
    conversation_id, visitor_token, ticket_id = _create_webchat_conversation(client)
    created = client.post(
        f"/api/webchat/conversations/{conversation_id}/voice/sessions",
        headers={"X-Webchat-Visitor-Token": visitor_token},
        json={},
    )
    voice_session_id = created.json()["voice_session_id"]

    response = client.post(
        f"/api/webchat/admin/tickets/{ticket_id}/voice/{voice_session_id}/accept",
        headers=_admin_headers(9204),
    )

    assert response.status_code == 403


def test_admin_voice_end_requires_auth():
    client = TestClient(app)
    conversation_id, visitor_token, ticket_id = _create_webchat_conversation(client)
    created = client.post(
        f"/api/webchat/conversations/{conversation_id}/voice/sessions",
        headers={"X-Webchat-Visitor-Token": visitor_token},
        json={},
    )
    voice_session_id = created.json()["voice_session_id"]

    response = client.post(f"/api/webchat/admin/tickets/{ticket_id}/voice/{voice_session_id}/end")

    assert response.status_code == 401


def test_voice_feature_disabled_rejects_public_create(monkeypatch):
    monkeypatch.setenv("WEBCHAT_VOICE_ENABLED", "false")
    client = TestClient(app)
    conversation_id, visitor_token, _ticket_id = _create_webchat_conversation(client)

    response = client.post(
        f"/api/webchat/conversations/{conversation_id}/voice/sessions",
        headers={"X-Webchat-Visitor-Token": visitor_token},
        json={},
    )

    assert response.status_code == 404


def test_livekit_room_is_closed_if_db_side_fails_after_room_creation(monkeypatch):
    created_rooms: list[str] = []
    closed_rooms: list[str] = []

    def fake_create_room(self, *, room_name: str) -> str:
        created_rooms.append(room_name)
        return room_name

    def fake_close_room(self, *, room_name: str) -> None:
        closed_rooms.append(room_name)

    def fake_issue_token(*args, **kwargs):
        raise RuntimeError("token mint failed")

    monkeypatch.setenv("WEBCHAT_VOICE_PROVIDER", "livekit")
    monkeypatch.setenv("LIVEKIT_URL", "wss://voice.example.test")
    monkeypatch.setenv("LIVEKIT_API_KEY", "unit_key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "unit_secret")
    monkeypatch.setenv("WEBCHAT_VOICE_CONNECT_SRC", "wss://voice.example.test https://voice.example.test")
    monkeypatch.setattr(LiveKitVoiceProvider, "create_room", fake_create_room)
    monkeypatch.setattr(LiveKitVoiceProvider, "close_room", fake_close_room)
    monkeypatch.setattr(webchat_voice_service, "_issue_token", fake_issue_token)

    client = TestClient(app, raise_server_exceptions=False)
    conversation_id, visitor_token, _ticket_id = _create_webchat_conversation(client, name="Compensate Visitor")

    created = client.post(
        f"/api/webchat/conversations/{conversation_id}/voice/sessions",
        headers={"X-Webchat-Visitor-Token": visitor_token},
        json={},
    )

    assert created.status_code == 500
    assert len(created_rooms) == 1
    assert closed_rooms == created_rooms

    db = SessionLocal()
    try:
        assert db.query(WebchatVoiceSession).filter(WebchatVoiceSession.provider_room_name == created_rooms[0]).count() == 0
    finally:
        db.close()
