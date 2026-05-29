from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ["APP_ENV"] = "development"
os.environ["DATABASE_URL"] = "sqlite:////tmp/webcall_operator_workbench_tests.db"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.auth_service import create_access_token
from app.db import Base, SessionLocal, engine
from app.enums import UserRole
from app.main import app
from app.models import Ticket, User
from app.services.webchat_handoff_service import request_webchat_handoff
from app.webchat_models import WebchatConversation


@pytest.fixture(autouse=True)
def reset_schema(monkeypatch):
    monkeypatch.setenv("WEBCHAT_VOICE_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_VOICE_PROVIDER", "mock")
    monkeypatch.setenv("WEBCHAT_VOICE_ALLOWED_PATH_PREFIXES", "/webchat/voice,/webcall")
    monkeypatch.setenv("WEBCHAT_VOICE_CONNECT_SRC", "wss://voice.example.test")
    monkeypatch.setenv("WEBCALL_AI_DEMO_LAB_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_DEMO_LAB_KILL_SWITCH", "false")
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        db.add_all([
            User(id=9701, username="webcall_admin", display_name="WebCall Admin", password_hash="test", role=UserRole.admin, is_active=True),
            User(id=9702, username="webcall_agent", display_name="WebCall Agent", password_hash="test", role=UserRole.agent, is_active=True),
        ])
        db.commit()
    finally:
        db.close()
    yield
    Base.metadata.drop_all(bind=engine)


def _headers(user_id: int = 9701) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def _create_context(client: TestClient) -> tuple[str, str, int]:
    created = client.post(
        "/api/webchat/init",
        json={
            "tenant_key": "pytest-webcall-workbench",
            "channel_key": "website",
            "visitor_name": "Ada Lovelace",
            "visitor_email": "ada@example.test",
            "visitor_phone": "+41 79 000 0000",
            "page_url": "https://example.test/support",
        },
    )
    assert created.status_code == 200, created.text
    payload = created.json()
    conversation_id = payload["conversation_id"]
    visitor_token = payload["visitor_token"]

    db = SessionLocal()
    try:
        conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == conversation_id).one()
        ticket = db.query(Ticket).filter(Ticket.id == conversation.ticket_id).one()
        ticket.ai_summary = "Customer is calling about a delivery exception."
        ticket.required_action = "Verify identity, review tracking facts, then decide whether to hand off or resume AI."
        ticket.missing_fields = "Confirm destination postal code."
        ticket.customer_update = "Customer expects a callback if the WebCall disconnects."
        request_webchat_handoff(
            db,
            conversation=conversation,
            ticket=ticket,
            source="pytest",
            trigger_type="voice_operator_workbench",
            reason_code="identity_and_delivery_review",
            reason_text="Customer moved from WebChat into WebCall.",
            recommended_agent_action="Verify identity and continue the WebCall with tracking evidence.",
            requested_by_actor_type="system",
        )
        db.commit()
        ticket_id = ticket.id
    finally:
        db.close()

    created_voice = client.post(
        f"/api/webchat/conversations/{conversation_id}/voice/sessions",
        headers={"X-Webchat-Visitor-Token": visitor_token},
        json={"locale": "de-CH", "recording_consent": False},
    )
    assert created_voice.status_code == 200, created_voice.text
    return conversation_id, created_voice.json()["voice_session_id"], ticket_id


def test_webcall_operator_workbench_requires_extended_guard():
    client = TestClient(app)

    unauthenticated = client.get("/api/webcall/operator/workbench")
    assert unauthenticated.status_code == 401

    forbidden = client.get("/api/webcall/operator/workbench", headers=_headers(9702))
    assert forbidden.status_code == 403
    detail = forbidden.json()["detail"]
    assert detail["code"] == "webcall_operator_workbench_requires_capability"
    assert "webcall.voice.queue.view" in detail["missing"]
    assert "webcall.voice.read" in detail["missing"]


def test_webcall_operator_workbench_aggregates_real_context_and_demo_status():
    client = TestClient(app)
    _conversation_id, voice_session_id, ticket_id = _create_context(client)

    response = client.get(f"/api/webcall/operator/workbench?ticket_id={ticket_id}&view=requested", headers=_headers())

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["selected_ticket_id"] == ticket_id
    assert payload["rows"][0]["source"] == "voice"
    assert payload["rows"][0]["handoff_request_id"]
    assert payload["rows"][0]["voice_session_id"] == voice_session_id
    selected = payload["selected"]
    assert selected["row"]["voice_session_id"] == voice_session_id
    assert selected["identity"]["verification_status"] == "matched"
    assert "email" in selected["identity"]["match_basis"]
    assert any(item["source"] == "handoff_or_ticket" for item in selected["ai_suggestions"])
    assert selected["handoff"]["recommended_agent_action"] == "Verify identity and continue the WebCall with tracking evidence."
    assert selected["voice_sessions"]["items"][0]["voice_session_id"] == voice_session_id
    assert payload["demo"]["visible"] is True
    assert payload["demo"]["status"]["status"] in {"ready", "disabled", "blocked"}
    assert "/api/webchat/admin/tickets/{ticket_id}/voice/{voice_session_id}/accept" in payload["source_contracts"]
    assert "/api/admin/webcall-ai-demo/status" in payload["source_contracts"]


def test_webcall_session_actions_write_timeline_audit_visible_in_workbench():
    client = TestClient(app)
    _conversation_id, voice_session_id, ticket_id = _create_context(client)

    accepted = client.post(f"/api/webchat/admin/tickets/{ticket_id}/voice/{voice_session_id}/accept", headers=_headers())
    assert accepted.status_code == 200, accepted.text
    ended = client.post(f"/api/webchat/admin/tickets/{ticket_id}/voice/{voice_session_id}/end", headers=_headers())
    assert ended.status_code == 200, ended.text

    response = client.get(f"/api/webcall/operator/workbench?ticket_id={ticket_id}&voice_status=closed_recent", headers=_headers())

    assert response.status_code == 200, response.text
    selected = response.json()["selected"]
    assert selected["voice_sessions"]["items"][0]["status"] == "ended"
    timeline_sources = {item.get("source_type") for item in selected["timeline"]["items"]}
    timeline_events = {item.get("event_type") for item in selected["timeline"]["items"]}
    assert "voice_call" in timeline_sources
    assert "webchat_event" in timeline_sources
    assert "voice.session.ended" in timeline_events
