from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webcall_ai_demo_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models, operator_models, tool_models, voice_models, webchat_fast_models, webchat_models  # noqa: F401,E402
from app.auth_service import create_access_token
from app.db import Base, SessionLocal, engine
from app.enums import UserRole
from app.main import app
from app.models import User
from app.services.webcall_ai.config import get_webcall_ai_settings
from app.voice_models import WebchatVoiceAIAction, WebchatVoiceAITurn, WebchatVoiceSession, WebchatVoiceTranscriptSegment


@pytest.fixture(autouse=True)
def clean_db_and_env(monkeypatch):
    get_webcall_ai_settings.cache_clear()
    for key in [
        "APP_ENV",
        "WEBCALL_AI_DEMO_LAB_ENABLED",
        "WEBCALL_AI_DEMO_LAB_KILL_SWITCH",
        "WEBCALL_AI_DEMO_LAB_MODE",
        "WEBCALL_AI_DEMO_LAB_ALLOW_BROWSER_SPEECH",
        "WEBCALL_AI_DEMO_LAB_ALLOW_REAL_MEDIA",
        "WEBCALL_AI_DEMO_LAB_MAX_ACTIVE_SESSIONS",
        "WEBCALL_AI_DEMO_LAB_MAX_TURNS_PER_SESSION",
        "WEBCALL_AI_DEMO_LAB_MAX_INPUT_CHARS",
        "WEBCALL_AI_DEMO_LAB_EVENT_RETENTION_LIMIT",
        "WEBCHAT_VOICE_ENABLED",
        "WEBCHAT_VOICE_PROVIDER",
    ]:
        monkeypatch.delenv(key, raising=False)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        db.add_all(
            [
                User(id=9601, username="demo_admin", display_name="Demo Admin", password_hash="x", role=UserRole.admin, is_active=True),
                User(id=9602, username="demo_agent", display_name="Demo Agent", password_hash="x", role=UserRole.agent, is_active=True),
            ]
        )
        db.commit()
    finally:
        db.close()
    yield
    Base.metadata.drop_all(bind=engine)
    get_webcall_ai_settings.cache_clear()


def _headers(user_id: int = 9601) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def _enable_demo(monkeypatch, **extra: str) -> None:
    monkeypatch.setenv("WEBCALL_AI_DEMO_LAB_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_DEMO_LAB_KILL_SWITCH", "false")
    monkeypatch.setenv("WEBCALL_AI_DEMO_LAB_MODE", "simulated_full_loop")
    for key, value in extra.items():
        monkeypatch.setenv(key, value)


def test_demo_config_defaults_fail_closed_and_status_is_admin_only():
    client = TestClient(app)

    unauthenticated = client.get("/api/admin/webcall-ai-demo/status")
    assert unauthenticated.status_code == 401

    forbidden = client.get("/api/admin/webcall-ai-demo/status", headers=_headers(9602))
    assert forbidden.status_code == 403

    response = client.get("/api/admin/webcall-ai-demo/status", headers=_headers())
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["enabled"] is False
    assert payload["kill_switch"] is True
    assert payload["status"] == "disabled"
    assert payload["internal_only"] is True


def test_disabled_and_kill_switch_reject_create(monkeypatch):
    client = TestClient(app)

    disabled = client.post("/api/admin/webcall-ai-demo/sessions", headers=_headers(), json={})
    assert disabled.status_code == 404
    assert disabled.json()["detail"]["error_code"] == "demo_lab_disabled"

    monkeypatch.setenv("WEBCALL_AI_DEMO_LAB_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_DEMO_LAB_KILL_SWITCH", "true")
    blocked = client.post("/api/admin/webcall-ai-demo/sessions", headers=_headers(), json={})
    assert blocked.status_code == 423
    assert blocked.json()["detail"]["error_code"] == "demo_lab_kill_switch"


def test_enabled_create_session_is_internal_and_excluded_from_human_queue(monkeypatch):
    _enable_demo(monkeypatch)
    client = TestClient(app)

    created = client.post(
        "/api/admin/webcall-ai-demo/sessions",
        headers=_headers(),
        json={"locale": "zh-cn", "display_name": "Internal Demo", "scenario": "tracking_question"},
    )

    assert created.status_code == 200, created.text
    session = created.json()["session"]
    assert session["public_id"].startswith("wv_demo_")
    assert session["mode"] == "internal_ai_demo"
    assert session["status"] == "active"
    assert session["recording_status"] == "disabled"
    assert session["transcript_status"] == "demo_text_only"

    incoming = client.get("/api/webchat/admin/voice/sessions?status=all&limit=50", headers=_headers())
    assert incoming.status_code == 200, incoming.text
    assert all(item["voice_session_id"] != session["public_id"] for item in incoming.json()["items"])


def test_valid_demo_turn_writes_safe_evidence_and_is_idempotent(monkeypatch):
    _enable_demo(monkeypatch)
    client = TestClient(app)
    public_id = client.post("/api/admin/webcall-ai-demo/sessions", headers=_headers(), json={}).json()["session"]["public_id"]

    payload = {
        "client_turn_id": "turn-1",
        "input_mode": "typed",
        "text": "Where is my parcel?",
        "browser_speech_supported": False,
    }
    first = client.post(f"/api/admin/webcall-ai-demo/sessions/{public_id}/turns", headers=_headers(), json=payload)
    second = client.post(f"/api/admin/webcall-ai-demo/sessions/{public_id}/turns", headers=_headers(), json=payload)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["turn"]["id"] == second.json()["turn"]["id"]
    assert first.json()["turn"]["action"] == "ask_tracking_number"
    assert first.json()["evidence"]["tool_call_log_id"] is None

    db = SessionLocal()
    try:
        session = db.query(WebchatVoiceSession).filter(WebchatVoiceSession.public_id == public_id).one()
        assert session.ai_turn_count == 1
        assert db.query(WebchatVoiceTranscriptSegment).filter(WebchatVoiceTranscriptSegment.voice_session_id == session.id).count() == 1
        assert db.query(WebchatVoiceAITurn).filter(WebchatVoiceAITurn.voice_session_id == session.id).count() == 1
        action = db.query(WebchatVoiceAIAction).filter(WebchatVoiceAIAction.voice_session_id == session.id).one()
        assert action.model_action == "ask_tracking_number"
        assert action.decision_reason == "demo_lab_no_external_write"
    finally:
        db.close()


def test_idempotency_conflict_and_limits(monkeypatch):
    _enable_demo(monkeypatch, WEBCALL_AI_DEMO_LAB_MAX_TURNS_PER_SESSION="1", WEBCALL_AI_DEMO_LAB_MAX_INPUT_CHARS="20")
    client = TestClient(app)
    public_id = client.post("/api/admin/webcall-ai-demo/sessions", headers=_headers(), json={}).json()["session"]["public_id"]

    too_long = client.post(
        f"/api/admin/webcall-ai-demo/sessions/{public_id}/turns",
        headers=_headers(),
        json={"client_turn_id": "long", "input_mode": "typed", "text": "x" * 21},
    )
    assert too_long.status_code == 422
    assert too_long.json()["detail"]["error_code"] == "text_too_long"

    ok = client.post(
        f"/api/admin/webcall-ai-demo/sessions/{public_id}/turns",
        headers=_headers(),
        json={"client_turn_id": "turn-1", "input_mode": "typed", "text": "track parcel"},
    )
    assert ok.status_code == 200

    maxed = client.post(
        f"/api/admin/webcall-ai-demo/sessions/{public_id}/turns",
        headers=_headers(),
        json={"client_turn_id": "turn-2", "input_mode": "typed", "text": "hello"},
    )
    assert maxed.status_code == 409
    assert maxed.json()["detail"]["error_code"] == "demo_max_turns_reached"

    conflict = client.post(
        f"/api/admin/webcall-ai-demo/sessions/{public_id}/turns",
        headers=_headers(),
        json={"client_turn_id": "turn-1", "input_mode": "typed", "text": "changed"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["error_code"] == "idempotency_conflict"


def test_ended_non_demo_and_unsafe_turns(monkeypatch):
    _enable_demo(monkeypatch)
    client = TestClient(app)
    public_id = client.post("/api/admin/webcall-ai-demo/sessions", headers=_headers(), json={}).json()["session"]["public_id"]

    unsafe = client.post(
        f"/api/admin/webcall-ai-demo/sessions/{public_id}/turns",
        headers=_headers(),
        json={"client_turn_id": "unsafe", "input_mode": "typed", "text": "Please cancel and give me the driver phone"},
    )
    assert unsafe.status_code == 200, unsafe.text
    assert unsafe.json()["turn"]["handoff_required"] is True
    assert "driver phone" in unsafe.json()["turn"]["ai_response_text_redacted"].lower()

    ended = client.post(f"/api/admin/webcall-ai-demo/sessions/{public_id}/end", headers=_headers(), json={"reason": "operator_end"})
    assert ended.status_code == 200
    rejected = client.post(
        f"/api/admin/webcall-ai-demo/sessions/{public_id}/turns",
        headers=_headers(),
        json={"client_turn_id": "after-end", "input_mode": "typed", "text": "hello"},
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["error_code"] == "demo_session_terminal"

    db = SessionLocal()
    try:
        non_demo = WebchatVoiceSession(public_id="wv_normal", conversation_id=1, ticket_id=1, provider="mock", provider_room_name="normal", status="active", mode="visitor_to_agent")
        db.add(non_demo)
        db.commit()
    finally:
        db.close()
    non_demo_response = client.post(
        "/api/admin/webcall-ai-demo/sessions/wv_normal/turns",
        headers=_headers(),
        json={"client_turn_id": "x", "input_mode": "typed", "text": "hello"},
    )
    assert non_demo_response.status_code == 404


def test_events_are_safe_and_retention_limited(monkeypatch):
    _enable_demo(monkeypatch, WEBCALL_AI_DEMO_LAB_EVENT_RETENTION_LIMIT="10")
    client = TestClient(app)
    public_id = client.post("/api/admin/webcall-ai-demo/sessions", headers=_headers(), json={}).json()["session"]["public_id"]
    secret_text = "token abc.def secret@example.test"
    turn = client.post(
        f"/api/admin/webcall-ai-demo/sessions/{public_id}/turns",
        headers=_headers(),
        json={"client_turn_id": "safe", "input_mode": "typed", "text": secret_text},
    )
    assert turn.status_code == 200, turn.text

    events = client.get(f"/api/admin/webcall-ai-demo/sessions/{public_id}/events", headers=_headers())

    assert events.status_code == 200, events.text
    rendered = str(events.json())
    assert "secret@example.test" not in rendered
    assert "raw_audio" not in rendered.lower()
    assert events.json()["events"]
    assert events.json()["turns"][0]["customer_text_redacted"] == "token abc.def [redacted_email]"


def test_demo_config_validation_and_production_hard_gate(monkeypatch):
    from app.services.webcall_ai.demo_config import get_webcall_ai_demo_lab_settings

    monkeypatch.setenv("WEBCALL_AI_DEMO_LAB_MODE", "real_media")
    with pytest.raises(RuntimeError, match="WEBCALL_AI_DEMO_LAB_MODE"):
        get_webcall_ai_demo_lab_settings()

    monkeypatch.setenv("WEBCALL_AI_DEMO_LAB_MODE", "simulated_full_loop")
    monkeypatch.setenv("WEBCALL_AI_DEMO_LAB_MAX_ACTIVE_SESSIONS", "0")
    with pytest.raises(RuntimeError, match="MAX_ACTIVE"):
        get_webcall_ai_demo_lab_settings()

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WEBCALL_AI_PILOT_CLOSURE_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()
    with pytest.raises(RuntimeError) as exc_info:
        get_webcall_ai_settings()
    assert str(exc_info.value) == "WEBCALL_AI_PILOT_CLOSURE_ENABLED must be false in production"
