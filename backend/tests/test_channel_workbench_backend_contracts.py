from __future__ import annotations

import json
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
from app.enums import ConversationState, EventType, JobStatus, MessageStatus, NoteVisibility, ResolutionCategory, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import BackgroundJob, Customer, OutboundEmailAccount, Team, Ticket, TicketAttachment, TicketEvent, TicketOutboundMessage, User  # noqa: E402
from app.services.webchat_handoff_service import request_webchat_handoff  # noqa: E402
from app.settings import get_settings  # noqa: E402
from app.webchat_models import WebchatAITurn, WebchatConversation, WebchatEvent, WebchatMessage  # noqa: E402


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


def _attach_webcall_operator_context(db_session, *, ticket_id: int, conversation_id: str):
    ticket = db_session.query(Ticket).filter(Ticket.id == ticket_id).one()
    conversation = db_session.query(WebchatConversation).filter(WebchatConversation.public_id == conversation_id).one()
    customer = Customer(name="Voice Visitor", email="voice@example.test", phone="+15550129999", external_ref="verified-voice")
    db_session.add(customer)
    db_session.flush()
    ticket.customer_id = customer.id
    ticket.tracking_number = "SPX-WEBCALL-123"
    ticket.ai_summary = "Customer needs a verified WebCall handoff for a delivery exception."
    ticket.missing_fields = "Confirm delivery address"
    ticket.customer_update = "We are checking the delivery exception with the carrier."
    ticket.required_action = "Verify identity before continuing the call."
    conversation.visitor_name = "Voice Visitor"
    conversation.visitor_email = "voice@example.test"
    conversation.visitor_phone = "+15550129999"
    message = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        direction="visitor",
        body="I want a human agent on the call.",
        body_text="I want a human agent on the call.",
        author_label="Voice Visitor",
    )
    db_session.add(message)
    db_session.flush()
    job = BackgroundJob(
        queue_name="webchat_ai_reply",
        job_type="webchat.ai_reply",
        payload_json="{}",
        dedupe_key=f"channel-workbench-webcall-ai-turn:{message.id}",
        status=JobStatus.pending,
    )
    db_session.add(job)
    db_session.flush()
    turn = WebchatAITurn(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        trigger_message_id=message.id,
        latest_visitor_message_id=message.id,
        job_id=job.id,
        status="bridge_calling",
        reply_source="codex",
        bridge_elapsed_ms=321,
        is_public_reply_allowed=True,
    )
    db_session.add(turn)
    db_session.flush()
    conversation.active_ai_turn_id = turn.id
    conversation.active_ai_status = "bridge_calling"
    conversation.active_ai_for_message_id = message.id
    handoff = request_webchat_handoff(
        db_session,
        conversation=conversation,
        ticket=ticket,
        source="ai_auto",
        trigger_type="webcall_operator_context",
        reason_code="identity_verification_required",
        recommended_agent_action="Verify identity, review the carrier exception, then continue the WebCall.",
        trigger_message_id=message.id,
        ai_turn_id=turn.id,
    )
    db_session.flush()
    return ticket, conversation, handoff, turn


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
    draft_payload = draft.json()
    assert draft_payload["status"] == "draft"
    assert draft_payload["provider_status"] == "draft_saved"
    assert draft_payload["mailbox_thread_id"] == f"<nexusdesk-ticket-{ticket.id}@nexusdesk.local>"
    assert draft_payload["mailbox_message_id"] is None
    assert draft_payload["mailbox_references"] == draft_payload["mailbox_thread_id"]
    assert [item["id"] for item in draft_payload["attachments"]] == [attachment.id]

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
    assert send_payload["mailbox_thread_id"] == draft_payload["mailbox_thread_id"]
    assert send_payload["mailbox_message_id"] == f"<nexusdesk-ticket-{ticket.id}-outbound-{send_payload['id']}@nexusdesk.local>"
    assert send_payload["mailbox_references"] == draft_payload["mailbox_thread_id"]

    timeline = client.get(f"/api/tickets/{ticket.id}/timeline?limit=20", headers=headers)
    assert timeline.status_code == 200, timeline.text
    items = timeline.json()["items"]
    outbound_items = [item for item in items if item.get("source_type") == "outbound_message"]
    event_types = {item.get("event_type") for item in items if item.get("source_type") == "ticket_event"}
    assert any(item["subject"] == "Draft subject" and item["status"] == "draft" for item in outbound_items)
    sent_timeline_item = next(item for item in outbound_items if item["subject"] == "Send subject" and item["status"] == "pending")
    assert any(item["payload"].get("attachments_count") == 1 for item in outbound_items)
    assert sent_timeline_item["mailbox_thread_id"] == send_payload["mailbox_thread_id"]
    assert sent_timeline_item["mailbox_message_id"] == send_payload["mailbox_message_id"]
    assert sent_timeline_item["payload"]["mailbox_thread_id"] == send_payload["mailbox_thread_id"]
    assert sent_timeline_item["payload"]["mailbox_message_id"] == send_payload["mailbox_message_id"]
    assert EventType.outbound_draft_saved.value in event_types
    assert EventType.outbound_queued.value in event_types

    rows = db_session.query(TicketOutboundMessage).filter(TicketOutboundMessage.ticket_id == ticket.id).all()
    assert {row.status for row in rows} == {MessageStatus.draft, MessageStatus.pending}
    draft_event = db_session.query(TicketEvent).filter(TicketEvent.ticket_id == ticket.id, TicketEvent.event_type == EventType.outbound_draft_saved).one()
    queued_event = db_session.query(TicketEvent).filter(TicketEvent.ticket_id == ticket.id, TicketEvent.event_type == EventType.outbound_queued).one()
    assert json.loads(draft_event.payload_json or "{}")["mailbox_thread_id"] == draft_payload["mailbox_thread_id"]
    assert json.loads(queued_event.payload_json or "{}")["mailbox_message_id"] == send_payload["mailbox_message_id"]


def test_email_dead_outbound_timeline_and_requeue_contract(client: TestClient, db_session):
    admin = _admin(db_session, user_id=9403)
    team = _team(db_session)
    ticket = _email_ticket(db_session, team=team)
    headers = _headers(admin)
    row = TicketOutboundMessage(
        ticket_id=ticket.id,
        channel=SourceChannel.email,
        status=MessageStatus.dead,
        subject="Dead provider message",
        body="provider failed",
        provider_status="dead:smtp_timeout",
        provider_message_id="nexusdesk-outbound-dead-contract",
        mailbox_thread_id=f"<nexusdesk-ticket-{ticket.id}@nexusdesk.local>",
        mailbox_message_id="<nexusdesk-ticket-dead-contract-outbound@example.test>",
        mailbox_references=f"<nexusdesk-ticket-{ticket.id}@nexusdesk.local>",
        retry_count=3,
        max_retries=3,
        failure_code="smtp_timeout",
        failure_reason="SMTP timed out",
        created_by=admin.id,
    )
    db_session.add(row)
    db_session.flush()
    db_session.commit()

    timeline = client.get(f"/api/tickets/{ticket.id}/timeline?limit=20", headers=headers)
    assert timeline.status_code == 200, timeline.text
    outbound = next(item for item in timeline.json()["items"] if item.get("source_type") == "outbound_message")
    assert outbound["source_id"] == row.id
    assert outbound["status"] == "dead"
    assert outbound["provider_status"] == "dead:smtp_timeout"
    assert outbound["retry_count"] == 3
    assert outbound["failure_code"] == "smtp_timeout"
    assert outbound["payload"]["provider_message_id"] == "nexusdesk-outbound-dead-contract"
    assert outbound["payload"]["mailbox_thread_id"] == f"<nexusdesk-ticket-{ticket.id}@nexusdesk.local>"
    assert outbound["payload"]["mailbox_message_id"] == "<nexusdesk-ticket-dead-contract-outbound@example.test>"
    assert outbound["payload"]["failure_reason"] == "SMTP timed out"
    db_session.commit()

    requeued = client.post(f"/api/admin/outbound/{row.id}/requeue", headers=headers)
    assert requeued.status_code == 200, requeued.text
    assert requeued.json()["status"] == "pending"
    db_session.refresh(row)
    assert row.status == MessageStatus.pending
    assert row.provider_status == "requeued_by_admin"
    assert row.retry_count == 0
    assert row.failure_code is None
    assert row.failure_reason is None
    assert row.mailbox_message_id == "<nexusdesk-ticket-dead-contract-outbound@example.test>"


def test_webcall_operator_workbench_real_api_identity_handoff_ai_and_session_contract(client: TestClient, db_session):
    admin = _admin(db_session, user_id=9404)
    headers = _headers(admin)

    init = client.post(
        "/api/webchat/init",
        json={
            "tenant_key": "channel-contract-webcall",
            "channel_key": "website",
            "visitor_name": "Voice Visitor",
            "visitor_email": "voice@example.test",
            "visitor_phone": "+15550129999",
            "page_url": "https://example.test/webcall",
        },
    )
    assert init.status_code == 200, init.text
    conversation_id = init.json()["conversation_id"]
    visitor_token = init.json()["visitor_token"]

    conversations = client.get("/api/webchat/admin/conversations", headers=headers)
    assert conversations.status_code == 200, conversations.text
    ticket_id = next(item["ticket_id"] for item in conversations.json() if item["conversation_id"] == conversation_id)
    ticket, conversation, handoff, turn = _attach_webcall_operator_context(db_session, ticket_id=ticket_id, conversation_id=conversation_id)
    db_session.add(
        WebchatEvent(
            conversation_id=conversation.id,
            ticket_id=ticket.id,
            event_type="webcall.operator.debug",
            payload_json=json.dumps({"participant_token": "secret-token", "nested": {"api_key": "secret-key"}, "safe": "visible"}, ensure_ascii=False),
        )
    )
    db_session.flush()

    summary = client.get(f"/api/tickets/{ticket.id}/summary", headers=headers)
    assert summary.status_code == 200, summary.text
    summary_payload = summary.json()
    assert summary_payload["customer"]["email"] == "voice@example.test"
    assert summary_payload["tracking_number"] == "SPX-WEBCALL-123"
    assert summary_payload["ai_summary"] == "Customer needs a verified WebCall handoff for a delivery exception."
    assert summary_payload["missing_fields"] == "Confirm delivery address"

    thread = client.get(f"/api/webchat/admin/tickets/{ticket.id}/thread", headers=headers)
    assert thread.status_code == 200, thread.text
    thread_payload = thread.json()
    assert thread_payload["visitor"]["email"] == "voice@example.test"
    assert thread_payload["handoff"]["id"] == handoff.id
    assert thread_payload["handoff"]["recommended_agent_action"] == "Verify identity, review the carrier exception, then continue the WebCall."
    assert thread_payload["ai_turns"][-1]["id"] == turn.id
    assert thread_payload["ai_turns"][-1]["status"] == "cancelled"
    assert {event["event_type"] for event in thread_payload["events"]} >= {"ai_turn.cancelled_by_handoff", "handoff.requested", "webcall.operator.debug"}
    debug_event = next(event for event in thread_payload["events"] if event["event_type"] == "webcall.operator.debug")
    assert debug_event["payload_json"]["participant_token"] == "[redacted]"
    assert debug_event["payload_json"]["nested"]["api_key"] == "[redacted]"
    assert debug_event["payload_json"]["safe"] == "visible"
    assert "secret-token" not in thread.text
    assert "secret-key" not in thread.text

    created = client.post(
        f"/api/webchat/conversations/{conversation.public_id}/voice/sessions",
        headers={"X-Webchat-Visitor-Token": visitor_token},
        json={"recording_consent": True},
    )
    assert created.status_code == 200, created.text
    voice_session_id = created.json()["voice_session_id"]

    queue = client.get("/api/webchat/admin/voice/sessions?status=incoming&limit=20", headers=headers)
    assert queue.status_code == 200, queue.text
    queue_item = next(item for item in queue.json()["items"] if item["voice_session_id"] == voice_session_id)
    assert queue_item["ticket_id"] == ticket.id
    assert queue_item["visitor_label"] == "Voice Visitor"
    assert "participant_token" not in queue_item

    accepted_call = client.post(f"/api/webchat/admin/tickets/{ticket.id}/voice/{voice_session_id}/accept", headers=headers)
    assert accepted_call.status_code == 200, accepted_call.text
    assert accepted_call.json()["participant_token"]

    accepted_handoff = client.post(
        f"/api/webchat/admin/handoff/{handoff.id}/accept",
        headers=headers,
        json={"note": "Accepted from WebCall operator workbench contract"},
    )
    assert accepted_handoff.status_code == 200, accepted_handoff.text
    assert accepted_handoff.json()["status"] == "accepted"
    released_handoff = client.post(
        f"/api/webchat/admin/handoff/{handoff.id}/release",
        headers=headers,
        json={"note": "Release after identity check"},
    )
    assert released_handoff.status_code == 200, released_handoff.text
    assert released_handoff.json()["status"] == "requested"
    resumed_ai = client.post(
        f"/api/webchat/admin/handoff/{handoff.id}/resume-ai",
        headers=headers,
        json={"note": "Resume after WebCall context review"},
    )
    assert resumed_ai.status_code == 200, resumed_ai.text
    assert resumed_ai.json()["status"] == "resumed_ai"

    ended_call = client.post(f"/api/webchat/admin/tickets/{ticket.id}/voice/{voice_session_id}/end", headers=headers)
    assert ended_call.status_code == 200, ended_call.text
    timeline = client.get(f"/api/tickets/{ticket.id}/timeline?limit=40", headers=headers)
    assert timeline.status_code == 200, timeline.text
    event_types = {item.get("event_type") for item in timeline.json()["items"] if item.get("source_type") == "webchat_event"}
    voice_items = [item for item in timeline.json()["items"] if item.get("source_type") == "voice_call" or item.get("kind") == "voice_call"]
    assert {"voice.session.created", "voice.session.accepted", "voice.session.ended", "handoff.accepted", "handoff.released", "ai.resumed"} <= event_types
    assert voice_items and voice_items[0]["payload"]["voice_session_id"] == voice_session_id
    assert db_session.query(WebchatEvent).filter(WebchatEvent.conversation_id == conversation.id, WebchatEvent.event_type == "ai.resumed").count() == 1


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
