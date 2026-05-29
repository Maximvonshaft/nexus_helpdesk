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
from app.enums import ConversationState, EventType, MessageStatus, NoteVisibility, ResolutionCategory, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Customer, OutboundEmailAccount, Team, Ticket, TicketAttachment, TicketEvent, TicketOutboundMessage, User  # noqa: E402
from app.settings import get_settings  # noqa: E402
from app.webchat_models import WebchatMessage  # noqa: E402


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


def _ticket_attachment(db_session, ticket: Ticket, *, uploaded_by: int | None = None) -> TicketAttachment:
    file_path = Path(__file__)
    row = TicketAttachment(
        ticket_id=ticket.id,
        uploaded_by=uploaded_by,
        file_name="email-proof.txt",
        file_path=str(file_path),
        mime_type="text/plain",
        file_size=file_path.stat().st_size,
        visibility=NoteVisibility.external,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_email_draft_send_and_timeline_audit_contract(client: TestClient, db_session):
    admin = _admin(db_session, user_id=9401)
    team = _team(db_session)
    ticket = _email_ticket(db_session, team=team)
    _smtp_account(db_session)
    attachment = _ticket_attachment(db_session, ticket, uploaded_by=admin.id)
    headers = _headers(admin)

    capabilities = client.get(f"/api/tickets/{ticket.id}/outbound/channels/capabilities", headers=headers)
    assert capabilities.status_code == 200, capabilities.text
    email_capability = next(item for item in capabilities.json()["channels"] if item["channel"] == "email")
    assert email_capability["supports_send"] is True
    assert email_capability["supports_attachments"] is True
    assert email_capability["external_send"] is True
    assert email_capability["missing"] == []

    draft = client.post(
        f"/api/tickets/{ticket.id}/outbound/draft",
        headers=headers,
        json={"channel": "email", "subject": "Draft subject", "body": "draft body", "attachment_ids": [attachment.id]},
    )
    assert draft.status_code == 200, draft.text
    assert draft.json()["status"] == "draft"
    assert draft.json()["provider_status"] == "draft_saved"
    assert [item["id"] for item in draft.json()["attachments"]] == [attachment.id]

    sent = client.post(
        f"/api/tickets/{ticket.id}/outbound/send",
        headers=headers,
        json={"channel": "email", "subject": "Send subject", "body": "send body", "attachment_ids": [attachment.id]},
    )
    assert sent.status_code == 200, sent.text
    send_payload = sent.json()
    assert send_payload["status"] == "pending"
    assert send_payload["provider_status"] == "queued"
    assert send_payload["delivery_semantics"] == "external_provider_send"
    assert send_payload["external_send"] is True
    assert send_payload["attachments_count"] == 1
    assert send_payload["attachment_ids"] == [attachment.id]

    timeline = client.get(f"/api/tickets/{ticket.id}/timeline?limit=20", headers=headers)
    assert timeline.status_code == 200, timeline.text
    items = timeline.json()["items"]
    outbound_items = [item for item in items if item.get("source_type") == "outbound_message"]
    event_types = {item.get("event_type") for item in items if item.get("source_type") == "ticket_event"}
    assert any(item["subject"] == "Draft subject" and item["status"] == "draft" for item in outbound_items)
    assert any(item["subject"] == "Send subject" and item["status"] == "pending" for item in outbound_items)
    assert any(item["payload"].get("attachments_count") == 1 for item in outbound_items)
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
