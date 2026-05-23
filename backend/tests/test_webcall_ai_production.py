from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webcall_ai_production_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.auth_service import create_access_token
from app.db import Base, SessionLocal, engine
from app.enums import UserRole
from app.main import app
from app.models import User
from app.services.webcall_ai_production.config import get_webcall_ai_production_settings
from app.services.webcall_ai_production.agent_worker import run_worker_once
from app.services.webcall_ai_production.orchestrator import run_fake_turn
from app.services.webcall_ai_production.tool_registry import default_registry
from app.voice_models import WebchatVoiceSession
from app.webchat_models import WebchatEvent  # noqa: F401 - ensure metadata registration


@pytest.fixture(scope="module", autouse=True)
def ensure_schema():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        user = User(id=9701, username="webcall_ai_admin", display_name="WebCall AI Admin", password_hash="test", role=UserRole.admin, is_active=True)
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
def webcall_ai_env(monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_PRODUCTION_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_AGENT_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_PROVIDER_PROFILE", "fake")
    monkeypatch.setenv("WEBCALL_AI_MAX_ACTIVE_SESSIONS", "20")
    monkeypatch.setenv("WEBCHAT_VOICE_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_VOICE_PROVIDER", "mock")
    monkeypatch.setenv("WEBCHAT_VOICE_ALLOWED_PATH_PREFIXES", "/webcall-ai,/webchat/voice")
    monkeypatch.setenv("STT_PROVIDER", "fake")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("TTS_PROVIDER", "fake")
    get_webcall_ai_production_settings.cache_clear()
    yield
    get_webcall_ai_production_settings.cache_clear()


def _admin_headers(user_id: int = 9701) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def test_runtime_config_redacts_livekit_secret(monkeypatch):
    monkeypatch.setenv("WEBCHAT_VOICE_PROVIDER", "livekit")
    monkeypatch.setenv("LIVEKIT_URL", "wss://voice.example.test")
    monkeypatch.setenv("LIVEKIT_API_KEY", "unit-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "unit-secret")
    monkeypatch.setenv("WEBCHAT_VOICE_CONNECT_SRC", "wss://voice.example.test")
    get_webcall_ai_production_settings.cache_clear()

    response = TestClient(app).get("/api/webcall-ai/runtime-config")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["enabled"] is True
    assert payload["voice_provider"] == "livekit"
    assert payload["livekit_url"] == "wss://voice.example.test"
    assert "unit-secret" not in response.text
    assert "unit-key" not in response.text


def test_default_runtime_is_fail_closed(monkeypatch):
    for name in [
        "WEBCALL_AI_PRODUCTION_ENABLED",
        "WEBCALL_AI_AGENT_ENABLED",
        "WEBCALL_AI_RECORD_RAW_AUDIO",
        "WEBCALL_AI_ALLOW_SPEEDAF_WORK_ORDER",
        "WEBCALL_AI_ALLOW_CANCEL",
        "WEBCALL_AI_ALLOW_ADDRESS_UPDATE",
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
    ]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WEBCHAT_VOICE_ENABLED", "false")
    monkeypatch.setenv("WEBCHAT_VOICE_PROVIDER", "mock")
    get_webcall_ai_production_settings.cache_clear()

    payload = get_webcall_ai_production_settings().public_runtime_config()

    assert payload["enabled"] is False
    assert payload["agent_enabled"] is False
    assert payload["status"] == "disabled"
    assert payload["record_raw_audio"] is False


def test_kill_switch_disables_runtime(monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_KILL_SWITCH", "true")
    get_webcall_ai_production_settings.cache_clear()

    payload = get_webcall_ai_production_settings().public_runtime_config()

    assert payload["status"] == "kill_switch"
    assert payload["agent_enabled"] is False


def test_external_provider_profile_requires_config(monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_PROVIDER_PROFILE", "external")
    monkeypatch.setenv("STT_PROVIDER", "external")
    monkeypatch.setenv("LLM_PROVIDER", "external")
    monkeypatch.setenv("TTS_PROVIDER", "external")
    get_webcall_ai_production_settings.cache_clear()

    with pytest.raises(ValueError, match="external WebCall AI providers"):
        get_webcall_ai_production_settings()


def test_production_rejects_inline_external_provider_secret(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("STT_API_KEY", "inline-secret")
    get_webcall_ai_production_settings.cache_clear()

    with pytest.raises(ValueError, match="STT_API_KEY"):
        get_webcall_ai_production_settings()


def test_worker_once_does_not_run_fake_heartbeat_when_disabled(monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_AGENT_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_KILL_SWITCH", "true")
    get_webcall_ai_production_settings.cache_clear()

    result = run_worker_once("unit-worker")

    assert result == {"claimed": 0, "processed": 0, "failed": 0, "status": "kill_switch"}


@pytest.mark.parametrize("flag", ["WEBCALL_AI_RECORD_RAW_AUDIO", "WEBCALL_AI_ALLOW_SPEEDAF_WORK_ORDER", "WEBCALL_AI_ALLOW_CANCEL", "WEBCALL_AI_ALLOW_ADDRESS_UPDATE"])
def test_unsafe_flags_are_rejected(monkeypatch, flag: str):
    monkeypatch.setenv(flag, "true")
    get_webcall_ai_production_settings.cache_clear()

    with pytest.raises(ValueError):
        get_webcall_ai_production_settings()


def test_create_session_is_idempotent_and_persists_ai_mode():
    client = TestClient(app)
    payload = {"visitor_name": "Voice Visitor", "page_url": "https://example.test/voice", "locale": "en"}
    idempotency_key = f"idem-webcall-ai-{uuid.uuid4().hex}"

    first = client.post("/api/webcall-ai/sessions", headers={"Idempotency-Key": idempotency_key}, json=payload)
    second = client.post("/api/webcall-ai/sessions", headers={"Idempotency-Key": idempotency_key}, json=payload)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json()["idempotent"] is True
    assert first.json()["session"]["public_id"] == second.json()["session"]["public_id"]
    assert first.json()["join"]["participant_token"].startswith("mock_voice_token_")

    session_id = first.json()["session"]["public_id"]
    db = SessionLocal()
    try:
        row = db.query(WebchatVoiceSession).filter(WebchatVoiceSession.public_id == session_id).one()
        assert row.mode == "livekit_ai_agent"
        assert row.ai_agent_status == "waiting_for_worker"
        assert row.recording_status == "disabled"
        event_types = [event.event_type for event in db.query(WebchatEvent).filter(WebchatEvent.conversation_id == row.conversation_id).all()]
        assert "webcall_ai.session.created" in event_types
    finally:
        db.close()


def test_idempotency_conflict_rejects_different_payload():
    client = TestClient(app)
    idempotency_key = f"idem-webcall-ai-conflict-{uuid.uuid4().hex}"
    first = client.post("/api/webcall-ai/sessions", headers={"Idempotency-Key": idempotency_key}, json={"visitor_name": "First"})
    second = client.post("/api/webcall-ai/sessions", headers={"Idempotency-Key": idempotency_key}, json={"visitor_name": "Second"})

    assert first.status_code == 200, first.text
    assert second.status_code == 409


def test_admin_session_events_require_auth():
    client = TestClient(app)
    created = client.post("/api/webcall-ai/sessions", headers={"Idempotency-Key": f"idem-admin-events-{uuid.uuid4().hex}"}, json={"visitor_name": "Admin Events"})
    assert created.status_code == 200, created.text
    session_id = created.json()["session"]["public_id"]

    unauthenticated = client.get(f"/api/admin/webcall-ai/sessions/{session_id}/events")
    authenticated = client.get(f"/api/admin/webcall-ai/sessions/{session_id}/events", headers=_admin_headers())

    assert unauthenticated.status_code == 401
    assert authenticated.status_code == 200, authenticated.text
    assert any(item["event_type"] == "webcall_ai.session.created" for item in authenticated.json()["events"])


def test_fake_orchestrator_and_tool_registry_contract():
    turn = run_fake_turn("Please track SF123456789CN", language="en")
    assert turn["response"]["intent"] == "tracking_lookup"
    assert turn["tool_result"]["decision"] == "allowed"
    blocked = default_registry().call("cancel_order", {"tracking_number": "SF123456789CN"})
    assert blocked["decision"] == "blocked"
