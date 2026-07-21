from __future__ import annotations

import os
import sys
import json
from datetime import timedelta
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
from app.enums import ConversationState, JobStatus, ResolutionCategory, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole
from app.main import app
from app.models import AdminAuditLog, BackgroundJob, Ticket, TicketEvent, TicketInternalNote, User, UserCapabilityOverride
from app.models_agent_routing import ConversationControl
from app.operator_models import OperatorQueueScopeGrant
from app.services import webchat_rate_limit as webchat_rate_limit_service
from app.services.background_jobs import SPEEDAF_VOICE_CALLBACK_JOB, process_background_job
from app.services.livekit_voice_provider import LiveKitVoiceProvider
from app.services.speedaf.action_service import SpeedafActionResult, SpeedafActionService
from app.services.voice_provider import VoiceParticipantToken
from app.utils.time import utc_now
from app.voice_models import WebchatVoiceAIAction, WebchatVoiceAITurn, WebchatVoiceSession, WebchatVoiceSessionAction, WebchatVoiceTranscriptSegment
from app.webchat_models import WebchatConversation, WebchatEvent, WebchatMessage  # noqa: F401 - ensure metadata registration


@pytest.fixture(scope="module", autouse=True)
def ensure_schema():
    Base.metadata.drop_all(bind=engine)
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
    monkeypatch.setenv("WEBCHAT_VOICE_ENABLED", "false")
    monkeypatch.setenv("WEBCHAT_HUMAN_CALL_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_VOICE_PROVIDER", "mock")
    monkeypatch.setenv("WEBCHAT_VOICE_ALLOWED_PATH_PREFIXES", "/webchat/voice")
    monkeypatch.setenv("WEBCHAT_VOICE_CONNECT_SRC", "wss://voice.example.test")
    monkeypatch.setattr(webchat_rate_limit_service.settings, "webchat_rate_limit_max_requests", 1000)
    webchat_rate_limit_service._MEMORY_BUCKETS.clear()
    yield


def _admin_headers(user_id: int = 9201) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def _create_webchat_conversation(
    client: TestClient,
    name: str = "Voice Visitor",
    *,
    create_ticket: bool = True,
) -> tuple[str, str, int | None]:
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
    if not create_ticket:
        return conversation_id, visitor_token, None

    db = SessionLocal()
    try:
        conversation = db.query(WebchatConversation).filter(
            WebchatConversation.public_id == conversation_id
        ).one()
        ticket = Ticket(
            ticket_no=f"VOICE-{conversation_id[-20:]}",
            title="Voice support follow-up",
            description="Explicit formal Ticket for ticket-scoped voice operator tests.",
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


def _authorize_ticketless_voice_scope(
    conversation_id: str,
    *,
    user_id: int = 9202,
    country_code: str = "ME",
) -> None:
    db = SessionLocal()
    try:
        conversation = db.query(WebchatConversation).filter(
            WebchatConversation.public_id == conversation_id
        ).one()
        control = db.query(ConversationControl).filter(
            ConversationControl.conversation_id == conversation.id
        ).one()
        control.country_code = country_code
        grant = db.query(OperatorQueueScopeGrant).filter(
            OperatorQueueScopeGrant.user_id == user_id,
            OperatorQueueScopeGrant.tenant_key == control.tenant_key,
            OperatorQueueScopeGrant.country_code == country_code,
            OperatorQueueScopeGrant.channel_key == control.channel_key,
        ).first()
        if grant is None:
            db.add(
                OperatorQueueScopeGrant(
                    user_id=user_id,
                    tenant_key=control.tenant_key,
                    country_code=country_code,
                    channel_key=control.channel_key,
                    enabled=True,
                    granted_by=user_id,
                )
            )
        else:
            grant.enabled = True
        db.commit()
    finally:
        db.close()


def _create_voice_session(client: TestClient, *, name: str = "Voice Visitor") -> tuple[str, str, int, str]:
    conversation_id, visitor_token, ticket_id = _create_webchat_conversation(client, name=name)
    assert ticket_id is not None
    created = client.post(
        f"/api/webchat/conversations/{conversation_id}/voice/sessions",
        headers={"X-Webchat-Visitor-Token": visitor_token},
        json={},
    )
    assert created.status_code == 200, created.text
    return conversation_id, visitor_token, ticket_id, created.json()["voice_session_id"]


def _set_voice_session_state(voice_session_id: str, *, status: str | None = None, expires_delta: timedelta | None = None) -> None:
    db = SessionLocal()
    try:
        row = db.query(WebchatVoiceSession).filter(WebchatVoiceSession.public_id == voice_session_id).one()
        now = utc_now()
        if status is not None:
            row.status = status
            if status in {"ended", "missed", "failed", "cancelled"}:
                row.ended_at = row.ended_at or now
        if expires_delta is not None:
            row.expires_at = now + expires_delta
        row.updated_at = now
        db.commit()
    finally:
        db.close()


def _set_voice_session_duration_fixture(voice_session_id: str) -> None:
    db = SessionLocal()
    try:
        row = db.query(WebchatVoiceSession).filter(WebchatVoiceSession.public_id == voice_session_id).one()
        now = utc_now()
        row.started_at = now - timedelta(seconds=45)
        row.ringing_at = now - timedelta(seconds=45)
        row.accepted_at = now - timedelta(seconds=30)
        row.active_at = now - timedelta(seconds=30)
        row.updated_at = now
        db.commit()
    finally:
        db.close()


def _event_types_for_ticket(ticket_id: int) -> list[str]:
    db = SessionLocal()
    try:
        return [event.event_type for event in db.query(WebchatEvent).filter(WebchatEvent.ticket_id == ticket_id).order_by(WebchatEvent.id.asc()).all()]
    finally:
        db.close()


def test_voice_runtime_config_exposes_livekit_url_without_secrets(monkeypatch):
    monkeypatch.setenv("WEBCHAT_VOICE_PROVIDER", "livekit")
    monkeypatch.setenv("LIVEKIT_URL", "wss://voice.example.test")
    monkeypatch.setenv("LIVEKIT_API_KEY", "unit_key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "unit_secret")
    monkeypatch.setenv("WEBCHAT_VOICE_CONNECT_SRC", "wss://voice.example.test https://voice.example.test")

    client = TestClient(app)
    response = client.get("/api/webchat/voice/runtime-config")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["enabled"] is True
    assert payload["provider"] == "livekit"
    assert payload["livekit_url"] == "wss://voice.example.test"
    assert "unit_secret" not in response.text
    assert "LIVEKIT_API_SECRET" not in response.text
    assert "unit_key" not in response.text


def test_public_create_voice_session_remains_ticketless():
    client = TestClient(app)
    conversation_id, visitor_token, ticket_id = _create_webchat_conversation(
        client,
        create_ticket=False,
    )
    assert ticket_id is None
    _authorize_ticketless_voice_scope(conversation_id)

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
    assert payload["recording_status"] == "disabled"
    assert payload["transcript_status"] == "disabled"
    assert payload["summary_status"] == "pending"

    db = SessionLocal()
    try:
        row = (
            db.query(WebchatVoiceSession)
            .filter(
                WebchatVoiceSession.public_id
                == payload["voice_session_id"]
            )
            .one()
        )
        conversation = (
            db.query(WebchatConversation)
            .filter(WebchatConversation.public_id == conversation_id)
            .one()
        )
        assert row.ticket_id is None
        assert conversation.ticket_id is None
        assert row.provider == "mock"
        events = (
            db.query(WebchatEvent)
            .filter(
                WebchatEvent.conversation_id == row.conversation_id,
                WebchatEvent.ticket_id.is_(None),
            )
            .all()
        )
        event_types = {event.event_type for event in events}
        assert "voice.session.created" in event_types
        assert "voice.session.ringing" in event_types
    finally:
        db.close()




def test_ticketless_session_can_be_accepted_and_ended_without_ticket_creation():
    client = TestClient(app)
    conversation_id, visitor_token, ticket_id = _create_webchat_conversation(
        client,
        name="Ticketless Voice Visitor",
        create_ticket=False,
    )
    assert ticket_id is None
    _authorize_ticketless_voice_scope(conversation_id)

    created = client.post(
        f"/api/webchat/conversations/{conversation_id}/voice/sessions",
        headers={"X-Webchat-Visitor-Token": visitor_token},
        json={},
    )
    assert created.status_code == 200, created.text
    voice_session_id = created.json()["voice_session_id"]

    accepted = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/accept",
        headers=_admin_headers(9202),
    )
    assert accepted.status_code == 200, accepted.text
    ended = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/end",
        headers=_admin_headers(9202),
    )
    assert ended.status_code == 200, ended.text

    db = SessionLocal()
    try:
        conversation = db.query(WebchatConversation).filter(
            WebchatConversation.public_id == conversation_id
        ).one()
        session = db.query(WebchatVoiceSession).filter(
            WebchatVoiceSession.public_id == voice_session_id
        ).one()
        assert conversation.ticket_id is None
        assert session.ticket_id is None
        final_message = db.query(WebchatMessage).filter(
            WebchatMessage.conversation_id == conversation.id,
            WebchatMessage.client_message_id == f"voice-call-ended:{voice_session_id}",
        ).one()
        assert final_message.ticket_id is None
    finally:
        db.close()





def test_ticketless_voice_rejects_missing_scope_before_session_creation():
    client = TestClient(app)
    conversation_id, visitor_token, ticket_id = _create_webchat_conversation(
        client,
        name="Unscoped Voice Visitor",
        create_ticket=False,
    )
    assert ticket_id is None

    response = client.post(
        f"/api/webchat/conversations/{conversation_id}/voice/sessions",
        headers={"X-Webchat-Visitor-Token": visitor_token},
        json={},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "conversation_scope_unavailable"
    db = SessionLocal()
    try:
        conversation = db.query(WebchatConversation).filter(
            WebchatConversation.public_id == conversation_id
        ).one()
        assert db.query(WebchatVoiceSession).filter(
            WebchatVoiceSession.conversation_id == conversation.id
        ).count() == 0
    finally:
        db.close()


def test_public_create_voice_session_rejects_invalid_token():
    client = TestClient(app)
    conversation_id, _visitor_token, _ticket_id = _create_webchat_conversation(client, create_ticket=False)

    created = client.post(
        f"/api/webchat/conversations/{conversation_id}/voice/sessions",
        headers={"X-Webchat-Visitor-Token": "invalid-token"},
        json={},
    )

    assert created.status_code == 403


def test_public_create_voice_session_returns_existing_active_session():
    client = TestClient(app)
    conversation_id, visitor_token, _ticket_id = _create_webchat_conversation(client, create_ticket=False)
    _authorize_ticketless_voice_scope(conversation_id)

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
    _conversation_id, _visitor_token, ticket_id, voice_session_id = _create_voice_session(client)

    accepted = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/accept",
        headers=_admin_headers(9202),
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "active"
    assert accepted.json()["accepted_by_user_id"] == 9202
    assert accepted.json().get("participant_token")

    accepted_again = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/accept",
        headers=_admin_headers(9202),
    )
    assert accepted_again.status_code == 200, accepted_again.text
    assert accepted_again.json()["status"] == "active"
    assert accepted_again.json()["accepted_by_user_id"] == 9202
    assert accepted_again.json().get("participant_token")

    second_accept = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/accept",
        headers=_admin_headers(9203),
    )
    assert second_accept.status_code == 409
    assert second_accept.json()["detail"] == "voice session already accepted by another agent"
    assert "participant_token" not in second_accept.text

    _set_voice_session_duration_fixture(voice_session_id)
    ended = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/end",
        headers=_admin_headers(9202),
    )
    assert ended.status_code == 200, ended.text
    ended_again = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/end",
        headers=_admin_headers(9202),
    )
    assert ended_again.status_code == 200, ended_again.text

    db = SessionLocal()
    try:
        row = db.query(WebchatVoiceSession).filter(WebchatVoiceSession.public_id == voice_session_id).one()
        assert row.accepted_by_user_id == 9202
        final_messages = db.query(WebchatMessage).filter(WebchatMessage.ticket_id == ticket_id, WebchatMessage.message_type == "voice_call").all()
        assert len(final_messages) == 1
        assert final_messages[0].client_message_id == f"voice-call-ended:{voice_session_id}"
        evidence = json.loads(final_messages[0].payload_json)
        assert evidence["voice_session_id"] == voice_session_id
        assert evidence["status"] == "ended"
        assert evidence["provider"] == "mock"
        assert evidence["accepted_by"] == 9202
        assert evidence["accepted_by_user_id"] == 9202
        assert evidence["ended_by"] == 9202
        assert evidence["ended_by_user_id"] == 9202
        assert evidence["ringing_duration_seconds"] >= 10
        assert evidence["talk_duration_seconds"] >= 20
        assert evidence["total_duration_seconds"] >= 40
        assert evidence["duration_seconds"] == evidence["total_duration_seconds"]
        assert evidence["recording_status"] == "disabled"
        assert evidence["transcript_status"] == "disabled"
        assert evidence["summary_status"] == "pending"
        event_types = [event.event_type for event in db.query(WebchatEvent).filter(WebchatEvent.ticket_id == ticket_id).all()]
        assert event_types.count("voice.session.accepted") == 1
        assert event_types.count("voice.session.active") == 1
        assert event_types.count("voice.session.ended") == 1
    finally:
        db.close()


def test_admin_voice_action_records_call_control_command_timeline_and_audit():
    client = TestClient(app)
    _conversation_id, _visitor_token, ticket_id, voice_session_id = _create_voice_session(client, name="Action Command Visitor")

    accepted = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/accept",
        headers=_admin_headers(9202),
    )
    assert accepted.status_code == 200, accepted.text

    keypad = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/actions",
        headers=_admin_headers(9202),
        json={"action_type": "keypad", "digits": "123456#", "note": "DTMF menu option"},
    )
    assert keypad.status_code == 200, keypad.text
    keypad_payload = keypad.json()
    assert keypad_payload["ok"] is True
    assert keypad_payload["voice_session_id"] == voice_session_id
    assert keypad_payload["action"]["action_type"] == "keypad"
    assert keypad_payload["action"]["provider_status"] == "not_executed"
    assert keypad_payload["action"]["provider_reason"] == "provider_adapter_pending"
    assert keypad_payload["action"]["payload"]["digits_length"] == 7
    assert "123456#" not in keypad.text

    transfer = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/actions",
        headers=_admin_headers(9202),
        json={"action_type": "transfer", "target": "tier-2-voice", "note": "Escalate to specialist queue"},
    )
    assert transfer.status_code == 200, transfer.text

    action_list = client.get(
        f"/api/webchat/admin/voice/{voice_session_id}/actions?limit=5",
        headers=_admin_headers(9202),
    )
    assert action_list.status_code == 200, action_list.text
    assert [item["action_type"] for item in action_list.json()["items"][:2]] == ["transfer", "keypad"]

    timeline = client.get(f"/api/tickets/{ticket_id}/timeline?limit=20", headers=_admin_headers(9202))
    assert timeline.status_code == 200, timeline.text
    assert any(item["source_type"] == "ticket_event" and item["event_type"] == "field_updated" and item.get("field_name") == "webcall.voice.action" for item in timeline.json()["items"])
    assert any(item["source_type"] == "webchat_event" and item["event_type"] == "voice.session.action_recorded" for item in timeline.json()["items"])

    db = SessionLocal()
    try:
        actions = db.query(WebchatVoiceSessionAction).filter(WebchatVoiceSessionAction.ticket_id == ticket_id).order_by(WebchatVoiceSessionAction.id.asc()).all()
        assert [row.action_type for row in actions] == ["keypad", "transfer"]
        keypad_row = actions[0]
        assert keypad_row.actor_user_id == 9202
        assert keypad_row.provider_status == "not_executed"
        assert keypad_row.provider_reason == "provider_adapter_pending"
        assert "123456#" not in (keypad_row.payload_json or "")
        ticket_event = db.query(TicketEvent).filter(TicketEvent.id == keypad_row.ticket_event_id).one()
        assert ticket_event.field_name == "webcall.voice.action"
        assert ticket_event.new_value == "keypad"
        assert "123456#" not in (ticket_event.payload_json or "")
        webchat_event = db.query(WebchatEvent).filter(WebchatEvent.id == keypad_row.webchat_event_id).one()
        assert webchat_event.event_type == "voice.session.action_recorded"
        audit = db.query(AdminAuditLog).filter(AdminAuditLog.id == keypad_row.audit_id).one()
        assert audit.action == "webcall.voice.action.keypad"
        assert audit.target_type == "webchat_voice_session_action"
        assert "123456#" not in (audit.new_value_json or "")
    finally:
        db.close()


def test_admin_voice_action_requires_control_capability_even_when_ticket_visible():
    client = TestClient(app)
    _conversation_id, _visitor_token, ticket_id, voice_session_id = _create_voice_session(client, name="Action RBAC Visitor")
    db = SessionLocal()
    try:
        ticket = db.query(Ticket).filter(Ticket.id == ticket_id).one()
        ticket.assignee_id = 9204
        db.commit()
    finally:
        db.close()

    response = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/actions",
        headers=_admin_headers(9204),
        json={"action_type": "mute"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "webcall_voice_control_requires_capability"


def test_admin_voice_note_writes_internal_note_timeline_webchat_event_and_audit():
    client = TestClient(app)
    _conversation_id, _visitor_token, ticket_id, voice_session_id = _create_voice_session(client, name="Call Note Visitor")

    response = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/notes",
        headers=_admin_headers(9202),
        json={"body": "  Customer verified name and requested a callback after delivery scan.  ", "source": "operator_workbench"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    assert payload["ticket_id"] == ticket_id
    assert payload["voice_session_id"] == voice_session_id
    assert payload["note_id"] > 0
    assert payload["ticket_event_id"] > 0
    assert payload["webchat_event_id"] > 0
    assert payload["audit_id"] > 0

    timeline = client.get(f"/api/tickets/{ticket_id}/timeline?limit=20", headers=_admin_headers(9202))
    assert timeline.status_code == 200, timeline.text
    timeline_items = timeline.json()["items"]
    assert any(item["source_type"] == "internal_note" and "Customer verified name" in item["body"] for item in timeline_items)
    assert any(item["source_type"] == "ticket_event" and item["event_type"] == "internal_note_added" for item in timeline_items)
    assert any(item["source_type"] == "webchat_event" and item["event_type"] == "voice.session.note_saved" for item in timeline_items)

    db = SessionLocal()
    try:
        note = db.query(TicketInternalNote).filter(TicketInternalNote.id == payload["note_id"]).one()
        assert note.ticket_id == ticket_id
        assert note.author_id == 9202
        assert note.body == "Customer verified name and requested a callback after delivery scan."
        ticket_event = db.query(TicketEvent).filter(TicketEvent.id == payload["ticket_event_id"]).one()
        assert ticket_event.event_type.value == "internal_note_added"
        assert ticket_event.note == "WebCall call note saved"
        event_payload = json.loads(ticket_event.payload_json)
        assert event_payload["voice_session_id"] == voice_session_id
        assert event_payload["note_id"] == note.id
        webchat_event = db.query(WebchatEvent).filter(WebchatEvent.id == payload["webchat_event_id"]).one()
        assert webchat_event.event_type == "voice.session.note_saved"
        webchat_payload = json.loads(webchat_event.payload_json)
        assert webchat_payload["voice_session_id"] == voice_session_id
        assert webchat_payload["author_id"] == 9202
        audit = db.query(AdminAuditLog).filter(AdminAuditLog.id == payload["audit_id"]).one()
        assert audit.action == "webcall.voice.note_saved"
        assert audit.target_type == "webchat_voice_session"
        audit_payload = json.loads(audit.new_value_json)
        assert audit_payload["ticket_id"] == ticket_id
        assert audit_payload["voice_session_id"] == voice_session_id
    finally:
        db.close()


def test_admin_voice_note_requires_voice_capability_even_when_ticket_visible():
    client = TestClient(app)
    _conversation_id, _visitor_token, ticket_id, voice_session_id = _create_voice_session(client, name="Note RBAC Visitor")
    db = SessionLocal()
    try:
        ticket = db.query(Ticket).filter(Ticket.id == ticket_id).one()
        ticket.assignee_id = 9204
        db.commit()
    finally:
        db.close()

    response = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/notes",
        headers=_admin_headers(9204),
        json={"body": "visible ticket but no voice permission"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "webcall_voice_read_requires_capability"


def test_admin_voice_evidence_returns_redacted_transcript_ai_turns_and_actions():
    client = TestClient(app)
    _conversation_id, _visitor_token, ticket_id, voice_session_id = _create_voice_session(client, name="Transcript Visitor")

    db = SessionLocal()
    try:
        session = db.query(WebchatVoiceSession).filter(WebchatVoiceSession.public_id == voice_session_id).one()
        session.transcript_status = "ready"
        session.summary_status = "ready"
        session.ai_agent_status = "ready"
        session.ai_turn_count = 1
        segment = WebchatVoiceTranscriptSegment(
            voice_session_id=session.id,
            conversation_id=session.conversation_id,
            ticket_id=ticket_id,
            provider=session.provider,
            provider_session_id=session.public_id,
            provider_item_id="segment-provider-1",
            participant_identity="visitor_voice_1",
            speaker_type="visitor",
            speaker_label="Customer",
            segment_id="segment-1",
            language="en",
            is_final=True,
            start_ms=100,
            end_ms=2400,
            text_raw="My phone is +15551234567 and tracking is SF123456789CN",
            text_redacted="My phone is [redacted_phone] and tracking is [redacted_tracking]",
            confidence=92,
            redaction_status="redacted",
        )
        db.add(segment)
        db.flush()
        turn = WebchatVoiceAITurn(
            voice_session_id=session.id,
            conversation_id=session.conversation_id,
            ticket_id=ticket_id,
            turn_index=1,
            customer_text_redacted=segment.text_redacted,
            ai_response_text_redacted="I can help with verified tracking after handoff.",
            language="en",
            intent="tracking",
            action="handoff",
            handoff_required=True,
            handoff_reason="requires_verified_order_lookup",
            confidence=88,
            provider="voice-ai",
            stt_provider="stt",
            tts_provider="tts",
            latency_ms=321,
        )
        db.add(turn)
        db.flush()
        db.add(WebchatVoiceAIAction(
            voice_session_id=session.id,
            turn_id=turn.id,
            model_action="handoff",
            nexus_decision="handoff",
            decision_reason="requires_verified_order_lookup",
            result_status="queued",
        ))
        db.commit()
    finally:
        db.close()

    response = client.get(
        f"/api/webchat/admin/voice/{voice_session_id}/evidence?limit=20",
        headers=_admin_headers(9202),
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    assert payload["ticket_id"] == ticket_id
    assert payload["voice_session_id"] == voice_session_id
    assert payload["transcript_status"] == "ready"
    assert payload["summary_status"] == "ready"
    assert payload["ai_agent_status"] == "ready"
    assert payload["ai_turn_count"] == 1
    assert payload["transcript_segments"][0]["text"] == "My phone is [redacted_phone] and tracking is [redacted_tracking]"
    assert "+15551234567" not in response.text
    assert "SF123456789CN" not in response.text
    assert payload["ai_turns"][0]["handoff_required"] is True
    assert payload["ai_turns"][0]["action"] == "handoff"
    assert payload["ai_actions"][0]["nexus_decision"] == "handoff"


def test_admin_voice_speedaf_callback_queues_and_worker_submits_without_leaking_waybill(monkeypatch):
    monkeypatch.setenv("SPEEDAF_VOICE_CALLBACK_ENABLED", "true")
    client = TestClient(app)
    _conversation_id, _visitor_token, ticket_id, voice_session_id = _create_voice_session(client, name="Speedaf Callback Visitor")

    body = {
        "callSessionId": "call-session-123",
        "isTransferredToHuman": True,
        "action": {
            "waybillCode": "WBVOICE12345",
            "action": "查询订单",
            "aiActionSummary": "AI checked the customer order status",
            "actionStatus": "SUCCESS",
            "errorCode": "",
        },
    }
    response = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/speedaf/callback",
        headers=_admin_headers(9202),
        json=body,
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["jobId"] > 0
    assert payload["ai_action_id"] > 0
    assert "WBVOICE12345" not in response.text

    calls = []

    def fake_send(self, payload):
        calls.append(payload)
        return SpeedafActionResult(ok=True, action_type="voice_callback", status="success", safe_payload={"ok": True})

    monkeypatch.setattr(SpeedafActionService, "send_voice_callback", fake_send)
    with SessionLocal() as db:
        job = db.query(BackgroundJob).filter(BackgroundJob.id == payload["jobId"]).one()
        assert job.job_type == SPEEDAF_VOICE_CALLBACK_JOB
        process_background_job(db, job)
        db.commit()

    assert calls == [{
        "callSessionId": "call-session-123",
        "isTransferredToHuman": 1,
        "action": {
            "waybillCode": "WBVOICE12345",
            "action": "查询订单",
            "actionTime": calls[0]["action"]["actionTime"],
            "aiActionSummary": "AI checked the customer order status",
            "actionStatus": "SUCCESS",
            "errorCode": "",
        },
    }]
    with SessionLocal() as db:
        assert db.query(WebchatVoiceAIAction).filter(WebchatVoiceAIAction.background_job_id == payload["jobId"], WebchatVoiceAIAction.speedaf_tool_name == "speedaf.voice.callback").count() == 1
        completed = db.query(TicketEvent).filter(TicketEvent.ticket_id == ticket_id, TicketEvent.field_name == "speedaf_voice_callback", TicketEvent.new_value == "completed").one()
        assert "WBVOICE12345" not in (completed.payload_json or "")
        completed_job = db.query(BackgroundJob).filter(BackgroundJob.id == payload["jobId"]).one()
        rendered_job_payload = completed_job.payload_json or ""
        assert "WBVOICE12345" not in rendered_job_payload
        assert "call-session-123" not in rendered_job_payload
        assert "AI checked the customer order status" not in rendered_job_payload
        safe_job_payload = json.loads(rendered_job_payload)
        assert safe_job_payload["scrubbed"] is True
        assert safe_job_payload["action"]["waybill_suffix"] == "2345"

    duplicate = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/speedaf/callback",
        headers=_admin_headers(9202),
        json=body,
    )
    assert duplicate.status_code == 200, duplicate.text
    duplicate_payload = duplicate.json()
    assert duplicate_payload["status"] == "already_submitted"
    assert duplicate_payload["jobId"] == payload["jobId"]
    assert duplicate_payload["ai_action_id"] is None
    with SessionLocal() as db:
        assert db.query(BackgroundJob).filter(BackgroundJob.dedupe_key == payload["dedupeKey"]).count() == 1


def test_admin_voice_speedaf_callback_requires_speedaf_write_capability(monkeypatch):
    monkeypatch.setenv("SPEEDAF_VOICE_CALLBACK_ENABLED", "true")
    client = TestClient(app)
    _conversation_id, _visitor_token, ticket_id, voice_session_id = _create_voice_session(client, name="Speedaf Callback RBAC Visitor")

    db = SessionLocal()
    try:
        db.query(UserCapabilityOverride).filter(
            UserCapabilityOverride.user_id == 9202,
            UserCapabilityOverride.capability == "tool:speedaf.voice.callback:write",
        ).delete()
        db.add(UserCapabilityOverride(user_id=9202, capability="tool:speedaf.voice.callback:write", allowed=False))
        db.commit()
    finally:
        db.close()

    try:
        response = client.post(
            f"/api/webchat/admin/voice/{voice_session_id}/speedaf/callback",
            headers=_admin_headers(9202),
            json={
                "action": {
                    "waybillCode": "WBVOICE12345",
                    "action": "查询订单",
                    "aiActionSummary": "AI checked the customer order status",
                    "actionStatus": "SUCCESS",
                },
            },
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "speedaf_voice_callback_requires_capability"
    finally:
        db = SessionLocal()
        try:
            db.query(UserCapabilityOverride).filter(
                UserCapabilityOverride.user_id == 9202,
                UserCapabilityOverride.capability == "tool:speedaf.voice.callback:write",
            ).delete()
            db.commit()
        finally:
            db.close()


def test_speedaf_voice_callback_non_retryable_failure_does_not_replay(monkeypatch):
    monkeypatch.setenv("SPEEDAF_VOICE_CALLBACK_ENABLED", "true")
    client = TestClient(app)
    _conversation_id, _visitor_token, ticket_id, voice_session_id = _create_voice_session(client, name="Speedaf Callback Nonretry Visitor")

    response = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/speedaf/callback",
        headers=_admin_headers(9202),
        json={
            "callSessionId": "call-session-nonretry",
            "action": {
                "waybillCode": "WBVOICE99999",
                "action": "查询订单",
                "aiActionSummary": "AI checked the customer order status",
                "actionStatus": "SUCCESS",
            },
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    calls = []

    def fake_send(self, submitted_payload):
        calls.append(submitted_payload)
        return SpeedafActionResult(
            ok=False,
            action_type="voice_callback",
            status="failed",
            error_code="sign_rule_not_configured",
            error_message="Speedaf sign rule is not configured",
            retryable=False,
            safe_payload={"error": "redacted"},
        )

    monkeypatch.setattr(SpeedafActionService, "send_voice_callback", fake_send)
    with SessionLocal() as db:
        job = db.query(BackgroundJob).filter(BackgroundJob.id == payload["jobId"]).one()
        process_background_job(db, job)
        db.commit()
        db.refresh(job)
        assert job.status == JobStatus.done
        assert job.attempt_count == 0
        assert job.last_error is None
        assert "WBVOICE99999" not in (job.payload_json or "")
        assert "call-session-nonretry" not in (job.payload_json or "")

    assert len(calls) == 1


def test_admin_voice_speedaf_callback_disabled_by_default():
    client = TestClient(app)
    _conversation_id, _visitor_token, ticket_id, voice_session_id = _create_voice_session(client, name="Disabled Speedaf Callback Visitor")

    response = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/speedaf/callback",
        headers=_admin_headers(9202),
        json={
            "action": {
                "waybillCode": "WBVOICE12345",
                "action": "查询订单",
                "aiActionSummary": "AI checked the customer order status",
                "actionStatus": "SUCCESS",
            },
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "speedaf_voice_callback_disabled"


def test_admin_voice_evidence_requires_voice_capability_even_when_ticket_visible():
    client = TestClient(app)
    _conversation_id, _visitor_token, ticket_id, voice_session_id = _create_voice_session(client, name="Evidence RBAC Visitor")
    db = SessionLocal()
    try:
        ticket = db.query(Ticket).filter(Ticket.id == ticket_id).one()
        ticket.assignee_id = 9204
        db.commit()
    finally:
        db.close()

    response = client.get(
        f"/api/webchat/admin/voice/{voice_session_id}/evidence",
        headers=_admin_headers(9204),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "webcall_voice_read_requires_capability"


def test_expired_ringing_session_accept_marks_missed_without_agent_token():
    client = TestClient(app)
    _conversation_id, _visitor_token, ticket_id, voice_session_id = _create_voice_session(client)
    _set_voice_session_state(voice_session_id, expires_delta=timedelta(seconds=-1))

    response = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/accept",
        headers=_admin_headers(9202),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "voice session expired"
    assert "participant_token" not in response.text

    db = SessionLocal()
    try:
        row = db.query(WebchatVoiceSession).filter(WebchatVoiceSession.public_id == voice_session_id).one()
        assert row.status == "missed"
        assert row.accepted_by_user_id is None
        assert row.ended_at is not None
    finally:
        db.close()
    assert "voice.session.missed" in _event_types_for_ticket(ticket_id)


def test_admin_voice_list_cleans_expired_ringing_session_to_missed_and_persists_evidence():
    client = TestClient(app)
    _conversation_id, _visitor_token, ticket_id, voice_session_id = _create_voice_session(client, name="Expired Queue Visitor")
    _set_voice_session_state(voice_session_id, expires_delta=timedelta(seconds=-1))

    response = client.get("/api/webchat/admin/voice/sessions?status=incoming&limit=20", headers=_admin_headers(9202))

    assert response.status_code == 200, response.text
    assert all(item["voice_session_id"] != voice_session_id for item in response.json()["items"])
    db = SessionLocal()
    try:
        row = db.query(WebchatVoiceSession).filter(WebchatVoiceSession.public_id == voice_session_id).one()
        assert row.status == "missed"
        assert row.ended_at is not None
        messages = db.query(WebchatMessage).filter(WebchatMessage.ticket_id == ticket_id, WebchatMessage.message_type == "voice_call").all()
        assert len(messages) == 1
        evidence = json.loads(messages[0].payload_json)
        assert evidence["voice_session_id"] == voice_session_id
        assert evidence["status"] == "missed"
        assert "ringing_duration_seconds" in evidence
        assert "talk_duration_seconds" in evidence
        assert "total_duration_seconds" in evidence
        events = [event.event_type for event in db.query(WebchatEvent).filter(WebchatEvent.ticket_id == ticket_id).all()]
        assert events.count("voice.session.missed") == 1
    finally:
        db.close()

    db = SessionLocal()
    try:
        persisted = db.query(WebchatVoiceSession).filter(WebchatVoiceSession.public_id == voice_session_id).one()
        assert persisted.status == "missed"
    finally:
        db.close()


@pytest.mark.parametrize(
    ("terminal_status", "detail"),
    [
        ("ended", "voice session ended"),
        ("missed", "voice session missed"),
        ("failed", "voice session failed"),
        ("cancelled", "voice session cancelled"),
    ],
)
def test_terminal_voice_sessions_cannot_be_accepted(terminal_status: str, detail: str):
    client = TestClient(app)
    _conversation_id, _visitor_token, ticket_id, voice_session_id = _create_voice_session(client, name=f"Terminal {terminal_status}")
    _set_voice_session_state(voice_session_id, status=terminal_status)

    response = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/accept",
        headers=_admin_headers(9202),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == detail
    assert "participant_token" not in response.text


def test_end_ringing_session_is_cancelled_and_idempotent_with_single_final_message():
    client = TestClient(app)
    _conversation_id, _visitor_token, ticket_id, voice_session_id = _create_voice_session(client, name="Cancel Visitor")

    first = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/end",
        headers=_admin_headers(9202),
    )
    second = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/end",
        headers=_admin_headers(9202),
    )

    assert first.status_code == 200, first.text
    assert first.json()["status"] == "cancelled"
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "cancelled"

    db = SessionLocal()
    try:
        messages = db.query(WebchatMessage).filter(WebchatMessage.ticket_id == ticket_id, WebchatMessage.message_type == "voice_call").all()
        assert len(messages) == 1
        events = [event.event_type for event in db.query(WebchatEvent).filter(WebchatEvent.ticket_id == ticket_id).all()]
        assert events.count("voice.session.cancelled") == 1
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
        f"/api/webchat/admin/voice/{voice_session_id}/accept",
        headers=_admin_headers(9202),
    )
    assert accepted.status_code == 200, accepted.text
    accepted_payload = accepted.json()
    assert accepted_payload["status"] == "active"
    assert accepted_payload["provider"] == "livekit"
    assert accepted_payload["participant_identity"].startswith("agent_")
    assert accepted_payload["participant_token"].startswith("fake_livekit_token::agent_")

    ended = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/end",
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
    _conversation_id, _visitor_token, ticket_id, voice_session_id = _create_voice_session(client)

    response = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/accept",
        headers=_admin_headers(9204),
    )

    assert response.status_code == 403


def test_admin_voice_accept_requires_voice_capability_even_when_ticket_visible():
    client = TestClient(app)
    _conversation_id, _visitor_token, ticket_id, voice_session_id = _create_voice_session(client)
    db = SessionLocal()
    try:
        ticket = db.query(Ticket).filter(Ticket.id == ticket_id).one()
        ticket.assignee_id = 9204
        db.commit()
    finally:
        db.close()

    response = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/accept",
        headers=_admin_headers(9204),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "webcall_voice_accept_requires_capability"


def test_admin_voice_queue_requires_voice_queue_capability():
    client = TestClient(app)

    response = client.get("/api/webchat/admin/voice/sessions?status=incoming&limit=20", headers=_admin_headers(9204))

    assert response.status_code == 403
    assert response.json()["detail"] == "webcall_voice_queue_requires_capability"


def test_admin_voice_end_requires_auth():
    client = TestClient(app)
    _conversation_id, _visitor_token, ticket_id, voice_session_id = _create_voice_session(client)

    response = client.post(f"/api/webchat/admin/voice/{voice_session_id}/end")

    assert response.status_code == 401


def test_voice_feature_disabled_rejects_public_create(monkeypatch):
    monkeypatch.setenv("WEBCHAT_VOICE_ENABLED", "false")
    monkeypatch.setenv("WEBCHAT_HUMAN_CALL_ENABLED", "false")
    client = TestClient(app)
    conversation_id, visitor_token, _ticket_id = _create_webchat_conversation(client)

    response = client.post(
        f"/api/webchat/conversations/{conversation_id}/voice/sessions",
        headers={"X-Webchat-Visitor-Token": visitor_token},
        json={},
    )

    assert response.status_code == 404
