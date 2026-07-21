from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_voice_p0_gap_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.auth_service import create_access_token
from app.db import Base, SessionLocal, engine
from app.enums import ConversationState, ResolutionCategory, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole
from app.main import app
from app.models import Ticket, User
from app.voice_models import WebchatVoiceSession
from app.webchat_models import WebchatConversation, WebchatEvent, WebchatMessage


def _admin_headers(user_id: int = 9301) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def _ensure_schema_and_users() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        for user in [
            User(id=9301, username="voice_p0_admin", display_name="Voice P0 Admin", password_hash="test", role=UserRole.admin, is_active=True),
            User(id=9302, username="voice_p0_agent", display_name="Voice P0 Agent", password_hash="test", role=UserRole.admin, is_active=True),
        ]:
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


def _create_webchat_conversation(client: TestClient, name: str) -> tuple[str, str, int]:
    init = client.post(
        "/api/webchat/init",
        json={"tenant_key": "pytest-voice-p0", "channel_key": "website", "visitor_name": name, "page_url": "https://example.test/help"},
    )
    assert init.status_code == 200, init.text
    payload = init.json()
    conversation_id = payload["conversation_id"]
    visitor_token = payload["visitor_token"]
    db = SessionLocal()
    try:
        conversation = db.query(WebchatConversation).filter(
            WebchatConversation.public_id == conversation_id
        ).one()
        ticket = Ticket(
            ticket_no=f"VOICE-P0-{conversation_id[-16:]}",
            title="Voice P0 formal follow-up",
            description="Explicit Ticket for ticket-scoped P0 voice tests.",
            source=TicketSource.user_message,
            source_channel=SourceChannel.web_chat,
            priority=TicketPriority.medium,
            status=TicketStatus.in_progress,
            resolution_category=ResolutionCategory.none,
            conversation_state=ConversationState.human_owned,
        )
        db.add(ticket)
        db.flush()
        conversation.ticket_id = ticket.id
        db.commit()
        return conversation_id, visitor_token, ticket.id
    finally:
        db.close()


def _create_voice_session(client: TestClient, name: str = "Voice P0 Visitor") -> tuple[str, str, int, str]:
    conversation_id, visitor_token, ticket_id = _create_webchat_conversation(client, name=name)
    created = client.post(
        f"/api/webchat/conversations/{conversation_id}/voice/sessions",
        headers={"X-Webchat-Visitor-Token": visitor_token},
        json={},
    )
    assert created.status_code == 200, created.text
    return conversation_id, visitor_token, ticket_id, created.json()["voice_session_id"]


def setup_module() -> None:
    os.environ["WEBCHAT_VOICE_ENABLED"] = "false"
    os.environ["WEBCHAT_HUMAN_CALL_ENABLED"] = "true"
    os.environ["WEBCHAT_LIVE_AI_VOICE_ENABLED"] = "false"
    os.environ["WEBCHAT_VOICE_PROVIDER"] = "mock"
    os.environ["WEBCHAT_VOICE_ALLOWED_PATH_PREFIXES"] = "/webchat/voice,/webcall"
    os.environ["WEBCHAT_VOICE_CONNECT_SRC"] = "wss://voice.example.test"
    _ensure_schema_and_users()


def test_admin_global_incoming_queue_hides_tokens():
    client = TestClient(app)
    _conversation_id, _visitor_token, ticket_id, voice_session_id = _create_voice_session(client, name="Queue Visitor")

    response = client.get("/api/webchat/admin/voice/sessions?status=ringing&limit=20", headers=_admin_headers(9301))

    assert response.status_code == 200, response.text
    item = next(item for item in response.json()["items"] if item["voice_session_id"] == voice_session_id)
    assert item["ticket_id"] == ticket_id
    assert item["status"] == "ringing"
    assert item["visitor_label"] == "Queue Visitor"
    assert "participant_token" not in item
    assert "participant_identity" not in item


def test_admin_reject_ringing_call_is_idempotent_and_writes_evidence():
    client = TestClient(app)
    _conversation_id, _visitor_token, ticket_id, voice_session_id = _create_voice_session(client, name="Reject Visitor")

    first = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/reject",
        headers=_admin_headers(9301),
        json={"reason": "agent unavailable"},
    )
    second = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/reject",
        headers=_admin_headers(9301),
        json={},
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["status"] == "cancelled"
    assert "participant_token" not in first.text

    db = SessionLocal()
    try:
        row = db.query(WebchatVoiceSession).filter(WebchatVoiceSession.public_id == voice_session_id).one()
        assert row.status == "cancelled"
        assert row.accepted_by_user_id is None
        assert row.ended_by_user_id == 9301
        messages = db.query(WebchatMessage).filter(WebchatMessage.ticket_id == ticket_id, WebchatMessage.message_type == "voice_call").all()
        assert len(messages) == 1
        events = [event.event_type for event in db.query(WebchatEvent).filter(WebchatEvent.ticket_id == ticket_id).all()]
        assert events.count("voice.session.rejected") == 1
    finally:
        db.close()


def test_ticket_timeline_contains_voice_call_after_end():
    client = TestClient(app)
    _conversation_id, _visitor_token, ticket_id, voice_session_id = _create_voice_session(client, name="Timeline Visitor")
    accepted = client.post(f"/api/webchat/admin/voice/{voice_session_id}/accept", headers=_admin_headers(9302))
    assert accepted.status_code == 200, accepted.text
    ended = client.post(f"/api/webchat/admin/voice/{voice_session_id}/end", headers=_admin_headers(9302))
    assert ended.status_code == 200, ended.text

    timeline = client.get(f"/api/tickets/{ticket_id}/timeline", headers=_admin_headers(9302))

    assert timeline.status_code == 200, timeline.text
    timeline_payload = timeline.json()
    timeline_items = timeline_payload["items"] if isinstance(timeline_payload, dict) else timeline_payload
    voice_items = [item for item in timeline_items if item.get("kind") == "voice_call" or item.get("source_type") == "voice_call"]
    assert len(voice_items) == 1
    assert voice_items[0]["payload"]["voice_session_id"] == voice_session_id
    assert voice_items[0]["payload"]["status"] == "ended"
    assert voice_items[0]["payload"]["accepted_by"] == 9302
    assert voice_items[0]["payload"]["ended_by"] == 9302
    assert "ringing_duration_seconds" in voice_items[0]["payload"]
    assert "talk_duration_seconds" in voice_items[0]["payload"]
    assert "total_duration_seconds" in voice_items[0]["payload"]
    assert voice_items[0]["payload"]["recording_status"] == "disabled"
    assert voice_items[0]["payload"]["transcript_status"] == "disabled"
    assert voice_items[0]["payload"]["summary_status"] == "pending"
