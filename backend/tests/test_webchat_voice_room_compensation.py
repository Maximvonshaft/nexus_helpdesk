from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_voice_compensation_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.auth_service import create_access_token
from app.db import Base, SessionLocal, engine
from app.enums import UserRole
from app.main import app
from app.models import User
from app.services.livekit_voice_provider import LiveKitVoiceProvider
from app.services.voice_provider import VoiceProviderError


@pytest.fixture(scope="module", autouse=True)
def ensure_schema():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        user = User(id=9301, username="voice_comp_admin", display_name="Voice Compensation Admin", password_hash="test", role=UserRole.admin, is_active=True)
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
    monkeypatch.setenv("WEBCHAT_VOICE_PROVIDER", "livekit")
    monkeypatch.setenv("WEBCHAT_VOICE_ALLOWED_PATH_PREFIXES", "/webchat/voice,/webcall")
    monkeypatch.setenv("LIVEKIT_URL", "wss://voice.example.test")
    monkeypatch.setenv("LIVEKIT_API_KEY", "unit_key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "unit_secret")
    monkeypatch.setenv("WEBCHAT_VOICE_CONNECT_SRC", "wss://voice.example.test https://voice.example.test")
    yield


def _admin_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(9301)}"}


def _create_webchat_conversation(client: TestClient) -> tuple[str, str]:
    init = client.post(
        "/api/webchat/init",
        json={
            "tenant_key": "pytest-voice-compensation",
            "channel_key": "website",
            "visitor_name": "Compensation Visitor",
            "page_url": "https://example.test/help",
        },
    )
    assert init.status_code == 200, init.text
    payload = init.json()
    canonical_list = client.get(
        "/api/support/conversations",
        params={"view": "all", "channel": "all", "limit": 100},
        headers=_admin_headers(),
    )
    assert canonical_list.status_code == 200, canonical_list.text
    assert any(
        item["session_key"].endswith(f":{payload['conversation_id']}")
        for item in canonical_list.json()["items"]
    )
    return payload["conversation_id"], payload["visitor_token"]


def test_livekit_room_is_closed_when_token_issuance_fails(monkeypatch):
    created_rooms: list[str] = []
    closed_rooms: list[str] = []

    def fake_create_room(self, *, room_name: str) -> str:
        created_rooms.append(room_name)
        return room_name

    def fake_close_room(self, *, room_name: str) -> None:
        closed_rooms.append(room_name)
        return None

    def fake_issue_token(self, *, room_name: str, participant_identity: str, ttl_seconds: int):
        raise VoiceProviderError("simulated token issuance failure")

    monkeypatch.setattr(LiveKitVoiceProvider, "create_room", fake_create_room)
    monkeypatch.setattr(LiveKitVoiceProvider, "close_room", fake_close_room)
    monkeypatch.setattr(LiveKitVoiceProvider, "issue_participant_token", fake_issue_token)

    client = TestClient(app, raise_server_exceptions=False)
    conversation_id, visitor_token = _create_webchat_conversation(client)

    response = client.post(
        f"/api/webchat/conversations/{conversation_id}/voice/sessions",
        headers={"X-Webchat-Visitor-Token": visitor_token},
        json={},
    )

    assert response.status_code >= 500, response.text
    assert len(created_rooms) == 1
    assert closed_rooms == created_rooms
    assert created_rooms[0].startswith("webcall_wv_")
