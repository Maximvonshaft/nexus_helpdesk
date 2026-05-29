from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")
os.environ.setdefault("WEBCHAT_VOICE_ENABLED", "true")
os.environ.setdefault("WEBCHAT_VOICE_PROVIDER", "mock")
os.environ.setdefault("WEBCHAT_VOICE_ALLOWED_PATH_PREFIXES", "/webchat,/webchat/voice,/webcall,/webchat-voice")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models as _models  # noqa: E402,F401
from app import voice_models as _voice_models  # noqa: E402,F401
from app import webchat_models as _webchat_models  # noqa: E402,F401
from app.auth_service import create_access_token  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.enums import ConversationState, EventType, MessageStatus, ResolutionCategory, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Customer, OutboundEmailAccount, Team, Ticket, TicketEvent, TicketOutboundMessage, User  # noqa: E402
from app.services.webchat_handoff_service import request_webchat_handoff  # noqa: E402
from app.settings import get_settings  # noqa: E402
from app.webchat_models import WebchatConversation, WebchatEvent, WebchatMessage  # noqa: E402


@pytest.fixture()
def db_session(tmp_path, monkeypatch):
    monkeypatch.setenv("ENABLE_OUTBOUND_DISPATCH", "true")
    monkeypatch.setenv("OUTBOUND_PROVIDER", "openclaw")
    monkeypatch.setenv("WEBCHAT_VOICE_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_VOICE_PROVIDER", "mock")
    get_settings.cache_clear()

    db_file = tmp_path / "channel_workbench_contracts.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False}, future=True)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
        get_settings.cache_clear()


@pytest.fixture()
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _uid() -> str:
    return uuid.uuid4().hex[:10]


def _admin(db_session, *, user_id: int = 9401) -> User:
    row = User(
        id=user_id,
        username=f"channel-contract-admin-{_uid()}",
        display_name="Channel Contract Admin",
        email=f"channel-contract-admin-{_uid()}@example.test",
        password_hash="test",
        role=UserRole.admin,
        is_active=True,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def _team(db_session) -> Team:
    row = Team(name=f"Support {_uid()}", team_type="support")
    db_session.add(row)
    db_session.flush()
    return row


def _email_ticket(db_session, *, team: Team) -> Ticket:
    customer = Customer(name="Email Customer", email="customer@example.com", phone="+15550123456")
    db_session.add(customer)
    db_session.flush()
    ticket = Ticket(
        ticket_no=f"EMAIL-{_uid()}",
        title="Email delivery update",
        description="Customer asks for an email update.",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.email,
        priority=TicketPriority.medium,
        status=TicketStatus.in_progress,
        resolution_category=ResolutionCategory.none,
        conversation_state=ConversationState.human_owned,
        team_id=team.id,
        source_chat_id="customer@example.com",
        preferred_reply_channel=SourceChannel.email.value,
        preferred_reply_contact="customer@example.com",
    )
    db_session.add(ticket)
    db_session.flush()
    return ticket


def _smtp_account(db_session) -> OutboundEmailAccount:
    row = OutboundEmailAccount(
        display_name="Support SMTP",
        host="smtp.example.test",
        port=587,
        username="support@example.test",
        password_encrypted="encrypted-secret",
        from_address="support@example.test",
        reply_to="reply@example.test",
        security_mode="starttls",
        is_active=True,
        priority=10,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_email_draft_send_and_timeline_audit_contract(client: TestClient, db_session):
    admin = _admin(db_session, user_id=9401)
    team = _team(db_session)
    ticket = _email_ticket(db_session, team=team)
    _smtp_account(db_session)
    headers = _headers(admin)

    capabilities = client.get(f"/api/tickets/{ticket.id}/outbound/channels/capabilities", headers=headers)
    assert capabilities.status_code == 200, capabilities.text
    email_capability = next(item for item in capabilities.json()["channels"] if item["channel"] == "email")
    assert email_capability["supports_send"] is True
    assert email_capability["external_send"] is True
    assert email_capability["missing"] == []

    draft = client.post(
        f"/api/tickets/{ticket.id}/outbound/draft",
        headers=headers,
        json={"channel": "email", "subject": "Draft subject", "body": "draft body"},
    )
    assert draft.status_code == 200, draft.text
    assert draft.json()["status"] == "draft"
    assert draft.json()["provider_status"] == "draft_saved"

    sent = client.post(
        f"/api/tickets/{ticket.id}/outbound/send",
        headers=headers,
        json={"channel": "email", "subject": "Send subject", "body": "send body"},
    )
    assert sent.status_code == 200, sent.text
    send_payload = sent.json()
    assert send_payload["status"] == "pending"
    assert send_payload["provider_status"] == "queued"
    assert send_payload["delivery_semantics"] == "external_provider_send"
    assert send_payload["external_send"] is True

    timeline = client.get(f"/api/tickets/{ticket.id}/timeline?limit=20", headers=headers)
    assert timeline.status_code == 200, timeline.text
    items = timeline.json()["items"]
    outbound_items = [item for item in items if item.get("source_type") == "outbound_message"]
    event_types = {item.get("event_type") for item in items if item.get("source_type") == "ticket_event"}
    assert any(item["subject"] == "Draft subject" and item["status"] == "draft" for item in outbound_items)
    assert any(item["subject"] == "Send subject" and item["status"] == "pending" for item in outbound_items)
    assert EventType.outbound_draft_saved.value in event_types
    assert EventType.outbound_queued.value in event_types

    rows = db_session.query(TicketOutboundMessage).filter(TicketOutboundMessage.ticket_id == ticket.id).all()
    assert {row.status for row in rows} == {MessageStatus.draft, MessageStatus.pending}
    assert db_session.query(TicketEvent).filter(TicketEvent.ticket_id == ticket.id, TicketEvent.event_type == EventType.outbound_draft_saved).count() == 1
    assert db_session.query(TicketEvent).filter(TicketEvent.ticket_id == ticket.id, TicketEvent.event_type == EventType.outbound_queued).count() == 1


def test_webcall_accept_end_writes_timeline_voice_evidence(client: TestClient, db_session):
    admin = _admin(db_session, user_id=9402)
    headers = _headers(admin)

    init = client.post(
        "/api/webchat/init",
        json={"tenant_key": "channel-contract", "channel_key": "website", "visitor_name": "Voice Visitor", "page_url": "https://example.test/help"},
    )
    assert init.status_code == 200, init.text
    init_payload = init.json()
    conversation_id = init_payload["conversation_id"]
    visitor_token = init_payload["visitor_token"]

    conversations = client.get("/api/webchat/admin/conversations", headers=headers)
    assert conversations.status_code == 200, conversations.text
    ticket_id = next(item["ticket_id"] for item in conversations.json() if item["conversation_id"] == conversation_id)

    created = client.post(
        f"/api/webchat/conversations/{conversation_id}/voice/sessions",
        headers={"X-Webchat-Visitor-Token": visitor_token},
        json={},
    )
    assert created.status_code == 200, created.text
    voice_session_id = created.json()["voice_session_id"]

    queue = client.get("/api/webchat/admin/voice/sessions?status=ringing&limit=20", headers=headers)
    assert queue.status_code == 200, queue.text
    queue_item = next(item for item in queue.json()["items"] if item["voice_session_id"] == voice_session_id)
    assert queue_item["ticket_id"] == ticket_id
    assert "participant_token" not in queue_item

    accepted = client.post(f"/api/webchat/admin/tickets/{ticket_id}/voice/{voice_session_id}/accept", headers=headers)
    assert accepted.status_code == 200, accepted.text
    ended = client.post(f"/api/webchat/admin/tickets/{ticket_id}/voice/{voice_session_id}/end", headers=headers)
    assert ended.status_code == 200, ended.text

    timeline = client.get(f"/api/tickets/{ticket_id}/timeline?limit=20", headers=headers)
    assert timeline.status_code == 200, timeline.text
    voice_items = [item for item in timeline.json()["items"] if item.get("source_type") == "voice_call" or item.get("kind") == "voice_call"]
    assert len(voice_items) == 1
    assert voice_items[0]["payload"]["voice_session_id"] == voice_session_id
    assert voice_items[0]["payload"]["status"] == "ended"
    assert voice_items[0]["payload"]["accepted_by"] == admin.id
    assert voice_items[0]["payload"]["ended_by"] == admin.id

    messages = db_session.query(WebchatMessage).filter(WebchatMessage.ticket_id == ticket_id, WebchatMessage.message_type == "voice_call").all()
    assert len(messages) == 1


def test_webcall_operator_workbench_backend_contract_covers_identity_handoff_ai_and_voice(client: TestClient, db_session):
    admin = _admin(db_session, user_id=9403)
    headers = _headers(admin)

    init = client.post(
        "/api/webchat/init",
        json={
            "tenant_key": "webcall-workbench-contract",
            "channel_key": "website",
            "visitor_name": "Identity Visitor",
            "visitor_email": "identity@example.test",
            "visitor_phone": "+15550112233",
            "page_url": "https://example.test/webcall",
        },
    )
    assert init.status_code == 200, init.text
    init_payload = init.json()
    conversation_id = init_payload["conversation_id"]
    visitor_token = init_payload["visitor_token"]

    conversations = client.get("/api/webchat/admin/conversations", headers=headers)
    assert conversations.status_code == 200, conversations.text
    ticket_id = next(item["ticket_id"] for item in conversations.json() if item["conversation_id"] == conversation_id)

    conversation = db_session.query(WebchatConversation).filter(WebchatConversation.public_id == conversation_id).one()
    ticket = db_session.query(Ticket).filter(Ticket.id == ticket_id).one()
    ticket.ai_summary = "Customer wants a secure callback before changing delivery details."
    ticket.required_action = "Verify identity, accept handoff, then handle the WebCall."
    ticket.missing_fields = "Confirm destination postcode."
    ticket.customer_update = "We are connecting a human operator."
    visitor_message = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        direction="visitor",
        body="Please call me, I need to verify my address.",
        body_text="Please call me, I need to verify my address.",
        author_label="Identity Visitor",
    )
    db_session.add(visitor_message)
    db_session.flush()
    handoff = request_webchat_handoff(
        db_session,
        conversation=conversation,
        ticket=ticket,
        source="customer_action",
        trigger_type="webcall_operator_contract",
        reason_code="identity_verification_required",
        recommended_agent_action="Confirm email/phone and take the WebCall.",
        trigger_message_id=visitor_message.id,
    )
    db_session.flush()

    created_voice = client.post(
        f"/api/webchat/conversations/{conversation_id}/voice/sessions",
        headers={"X-Webchat-Visitor-Token": visitor_token},
        json={},
    )
    assert created_voice.status_code == 200, created_voice.text
    voice_session_id = created_voice.json()["voice_session_id"]

    voice_queue = client.get("/api/webchat/admin/voice/sessions?status=incoming&limit=20", headers=headers)
    assert voice_queue.status_code == 200, voice_queue.text
    voice_item = next(item for item in voice_queue.json()["items"] if item["voice_session_id"] == voice_session_id)
    assert voice_item["ticket_id"] == ticket_id
    assert voice_item["visitor_label"] == "Identity Visitor"
    assert "participant_token" not in voice_item

    handoff_queue = client.get("/api/webchat/admin/handoff/queue?view=requested&limit=20", headers=headers)
    assert handoff_queue.status_code == 200, handoff_queue.text
    handoff_item = next(item for item in handoff_queue.json()["items"] if item["id"] == handoff.id)
    assert handoff_item["ticket_id"] == ticket_id
    assert handoff_item["recommended_agent_action"] == "Confirm email/phone and take the WebCall."
    assert handoff_item["can_accept"] is True

    thread = client.get(f"/api/webchat/admin/tickets/{ticket_id}/thread", headers=headers)
    assert thread.status_code == 200, thread.text
    thread_payload = thread.json()
    assert thread_payload["visitor"]["email"] == "identity@example.test"
    assert thread_payload["visitor"]["phone"] == "+15550112233"
    assert thread_payload["handoff"]["id"] == handoff.id
    assert thread_payload["handoff"]["recommended_agent_action"] == "Confirm email/phone and take the WebCall."

    summary = client.get(f"/api/tickets/{ticket_id}/summary", headers=headers)
    assert summary.status_code == 200, summary.text
    summary_payload = summary.json()
    assert summary_payload["customer"]["email"] == "identity@example.test"
    assert summary_payload["required_action"] == "Confirm email/phone and take the WebCall."
    assert summary_payload["ai_summary"] == "Customer wants a secure callback before changing delivery details."

    accepted_handoff = client.post(f"/api/webchat/admin/handoff/{handoff.id}/accept", headers=headers, json={"note": "Accepted from WebCall workbench contract"})
    assert accepted_handoff.status_code == 200, accepted_handoff.text
    assert accepted_handoff.json()["status"] == "accepted"

    accepted_voice = client.post(f"/api/webchat/admin/tickets/{ticket_id}/voice/{voice_session_id}/accept", headers=headers)
    assert accepted_voice.status_code == 200, accepted_voice.text
    assert accepted_voice.json()["status"] == "active"
    ended_voice = client.post(f"/api/webchat/admin/tickets/{ticket_id}/voice/{voice_session_id}/end", headers=headers)
    assert ended_voice.status_code == 200, ended_voice.text
    assert ended_voice.json()["status"] == "ended"

    timeline = client.get(f"/api/tickets/{ticket_id}/timeline?limit=20", headers=headers)
    assert timeline.status_code == 200, timeline.text
    voice_items = [item for item in timeline.json()["items"] if item.get("source_type") == "voice_call" or item.get("kind") == "voice_call"]
    assert len(voice_items) == 1
    assert voice_items[0]["payload"]["voice_session_id"] == voice_session_id
    assert voice_items[0]["payload"]["status"] == "ended"

    webchat_event_types = {
        row.event_type
        for row in db_session.query(WebchatEvent).filter(WebchatEvent.conversation_id == conversation.id).all()
    }
    assert {"handoff.requested", "handoff.accepted", "voice.session.accepted", "voice.session.ended"}.issubset(webchat_event_types)
