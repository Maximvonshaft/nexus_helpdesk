from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webcall_operator_workbench_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.auth_service import create_access_token
from app.db import Base, SessionLocal, engine
from app.enums import UserRole
from app.main import app
from app.models import User


@pytest.fixture(scope="module", autouse=True)
def ensure_schema():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        users = [
            User(id=9301, username="webcall_operator_admin", display_name="WebCall Operator Admin", password_hash="test", role=UserRole.admin, is_active=True),
            User(id=9302, username="webcall_operator_agent", display_name="WebCall Operator Agent", password_hash="test", role=UserRole.agent, is_active=True),
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
    monkeypatch.setenv("WEBCHAT_VOICE_ALLOWED_PATH_PREFIXES", "/webchat/voice,/webcall")
    monkeypatch.setenv("WEBCHAT_VOICE_CONNECT_SRC", "wss://voice.example.test")
    yield


def _headers(user_id: int = 9301) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def _create_webchat_conversation(client: TestClient) -> tuple[str, str, int]:
    init = client.post(
        "/api/webchat/init",
        json={
            "tenant_key": "pytest-webcall-operator",
            "channel_key": "website",
            "visitor_name": "Operator Visitor",
            "visitor_email": "operator.visitor@example.test",
            "page_url": "https://example.test/webcall",
        },
    )
    assert init.status_code == 200, init.text
    payload = init.json()
    conversation_id = payload["conversation_id"]
    visitor_token = payload["visitor_token"]

    conversations = client.get("/api/webchat/admin/conversations", headers=_headers())
    assert conversations.status_code == 200, conversations.text
    ticket_id = next(item["ticket_id"] for item in conversations.json() if item["conversation_id"] == conversation_id)
    return conversation_id, visitor_token, ticket_id


def _create_voice_session(client: TestClient) -> tuple[str, int, str]:
    conversation_id, visitor_token, ticket_id = _create_webchat_conversation(client)
    created = client.post(
        f"/api/webchat/conversations/{conversation_id}/voice/sessions",
        headers={"X-Webchat-Visitor-Token": visitor_token},
        json={},
    )
    assert created.status_code == 200, created.text
    return conversation_id, ticket_id, created.json()["voice_session_id"]


def test_operator_workbench_endpoint_unifies_queue_identity_handoff_demo_and_session_actions():
    client = TestClient(app)
    _conversation_id, ticket_id, voice_session_id = _create_voice_session(client)
    forced = client.post(
        f"/api/webchat/admin/tickets/{ticket_id}/force-takeover",
        headers=_headers(),
        json={"reason_code": "webcall_operator_test", "note": "Operator test takeover"},
    )
    assert forced.status_code == 200, forced.text

    response = client.get(
        f"/api/admin/webcall-ai/operator-workbench?ticket_id={ticket_id}&handoff_view=mine&voice_status=incoming&limit=20",
        headers=_headers(),
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    assert payload["route"]["operator"] == "/webcall/operator"
    assert payload["contracts"]["voice_accept"].endswith("/voice/{voice_session_id}/accept")
    assert payload["capabilities"]["voice_queue_view"] is True
    assert payload["capabilities"]["handoff_force_takeover"] is True
    assert payload["demo"]["available"] is True
    assert payload["voice_queue"]["items"][0]["voice_session_id"] == voice_session_id
    assert payload["handoff_queue"]["view"] == "mine"
    assert payload["selected"]["ticket"]["id"] == ticket_id
    assert payload["selected"]["identity"]["verified"] is True
    assert "name" in payload["selected"]["identity"]["matches"]
    assert payload["selected"]["handoff"]["status"] == "accepted"
    assert any(item["source"] == "handoff_recommendation" for item in payload["selected"]["ai_suggestions"])
    assert payload["selected"]["session_actions"]["items"][0]["voice_session_id"] == voice_session_id
    assert payload["selected"]["session_actions"]["items"][0]["can_accept"] is True
    assert payload["selected"]["session_actions"]["items"][0]["can_end"] is True
    assert payload["selected"]["timeline_audit"]["writeback_sources"]["webchat_event_count"] >= 1
    assert "participant_token" not in response.text


def test_operator_workbench_requires_webcall_queue_capability():
    client = TestClient(app)

    response = client.get("/api/admin/webcall-ai/operator-workbench", headers=_headers(9302))

    assert response.status_code == 403
    assert response.json()["detail"] == "webcall_voice_queue_requires_capability"
