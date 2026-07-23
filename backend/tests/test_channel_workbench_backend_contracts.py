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
os.environ.setdefault("WEBCHAT_VOICE_ENABLED", "false")
os.environ.setdefault("WEBCHAT_HUMAN_CALL_ENABLED", "true")
os.environ.setdefault("WEBCHAT_LIVE_AI_VOICE_ENABLED", "false")
os.environ.setdefault("WEBCHAT_VOICE_PROVIDER", "mock")
os.environ.setdefault(
    "WEBCHAT_VOICE_ALLOWED_PATH_PREFIXES",
    "/webchat,/webchat/voice,/webcall,/webchat-voice",
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models as _models  # noqa: E402,F401
from app import models_agent_routing as _models_agent_routing  # noqa: E402,F401
from app import operator_models as _operator_models  # noqa: E402,F401
from app import voice_models as _voice_models  # noqa: E402,F401
from app import webchat_models as _webchat_models  # noqa: E402,F401
from app.auth_service import create_access_token  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.enums import (  # noqa: E402
    ConversationState,
    EventType,
    MessageStatus,
    NoteVisibility,
    ResolutionCategory,
    SourceChannel,
    TicketPriority,
    TicketSource,
    TicketStatus,
    UserRole,
)
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    AdminAuditLog,
    Customer,
    OutboundEmailAccount,
    Team,
    Ticket,
    TicketAttachment,
    TicketEvent,
    TicketInboundEmailMessage,
    TicketOutboundMessage,
    User,
)
from app.models_agent_routing import ConversationControl  # noqa: E402
from app.operator_models import OperatorQueueScopeGrant  # noqa: E402
from app.services import support_sensitive_access  # noqa: E402
from app.services.agent_routing_service import set_agent_state  # noqa: E402
from app.settings import get_settings  # noqa: E402
from app.voice_models import (  # noqa: E402
    VoiceRoutingOffer,
    WebchatVoiceSession,
    WebchatVoiceSessionAction,
)
from app.webchat_models import (  # noqa: E402
    WebchatConversation,
    WebchatEvent,
    WebchatHandoffRequest,
    WebchatMessage,
)


@pytest.fixture()
def db_session(tmp_path, monkeypatch):
    monkeypatch.setenv("ENABLE_OUTBOUND_DISPATCH", "true")
    monkeypatch.setenv("OUTBOUND_PROVIDER", "native")
    monkeypatch.setenv("WEBCHAT_VOICE_ENABLED", "false")
    monkeypatch.setenv("WEBCHAT_HUMAN_CALL_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_LIVE_AI_VOICE_ENABLED", "false")
    monkeypatch.setenv("WEBCHAT_VOICE_PROVIDER", "mock")
    get_settings.cache_clear()

    db_file = tmp_path / "channel_workbench_contracts.db"
    engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    TestingSession = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        future=True,
        expire_on_commit=False,
    )
    monkeypatch.setattr(support_sensitive_access, "SessionLocal", TestingSession)
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
    customer = Customer(
        name="Email Customer",
        email="customer@example.com",
        phone="+15550123456",
    )
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


def _ticket_attachment(
    db_session,
    ticket: Ticket,
    *,
    uploaded_by: int | None = None,
) -> TicketAttachment:
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


def _configure_webcall_operator(
    db_session,
    *,
    user: User,
    conversation: WebchatConversation,
    ticket: Ticket | None = None,
    country_code: str = "ME",
) -> ConversationControl:
    control = (
        db_session.query(ConversationControl)
        .filter(ConversationControl.conversation_id == conversation.id)
        .one()
    )
    control.country_code = country_code
    if ticket is not None:
        conversation.ticket_id = ticket.id
        ticket.assignee_id = user.id
    db_session.add(
        OperatorQueueScopeGrant(
            user_id=user.id,
            tenant_key=control.tenant_key,
            country_code=country_code,
            channel_key=control.channel_key,
            enabled=True,
            granted_by=user.id,
        )
    )
    set_agent_state(
        db_session,
        user=user,
        presence_status="online",
        max_concurrent_conversations=3,
        voice_enabled=True,
        max_concurrent_voice_calls=1,
        voice_wrap_up_seconds=0,
    )
    db_session.flush()
    return control


def _init_webcall(
    client: TestClient,
    db_session,
    *,
    admin: User,
    with_ticket: bool,
) -> tuple[WebchatConversation, Ticket | None, str]:
    initialized = client.post(
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
    assert initialized.status_code == 200, initialized.text
    payload = initialized.json()
    conversation = (
        db_session.query(WebchatConversation)
        .filter(WebchatConversation.public_id == payload["conversation_id"])
        .one()
    )
    ticket = None
    if with_ticket:
        customer = Customer(
            name="Voice Visitor",
            email="voice@example.test",
            phone="+15550129999",
            external_ref=f"verified-voice-{_uid()}",
        )
        db_session.add(customer)
        db_session.flush()
        ticket = Ticket(
            ticket_no=f"WEBCALL-{_uid()}",
            title="WebCall formal follow-up",
            description="Explicit Ticket for ticket-scoped WebCall operations.",
            customer_id=customer.id,
            source=TicketSource.user_message,
            source_channel=SourceChannel.web_chat,
            priority=TicketPriority.medium,
            status=TicketStatus.in_progress,
            resolution_category=ResolutionCategory.none,
            conversation_state=ConversationState.human_owned,
            tracking_number="SPX-WEBCALL-123",
            ai_summary=(
                "Customer needs a verified WebCall handoff for a delivery exception."
            ),
            missing_fields="Confirm delivery address",
            required_action="Verify identity before continuing the call.",
        )
        db_session.add(ticket)
        db_session.flush()
    _configure_webcall_operator(
        db_session,
        user=admin,
        conversation=conversation,
        ticket=ticket,
    )
    db_session.commit()
    return conversation, ticket, payload["visitor_token"]


def test_email_mailbox_queue_projection_uses_real_mailbox_rows(
    client: TestClient,
    db_session,
):
    admin = _admin(db_session, user_id=9401)
    auditor = User(
        username=f"channel-contract-auditor-{_uid()}",
        display_name="Channel Contract Auditor",
        email=f"channel-contract-auditor-{_uid()}@example.test",
        password_hash="test",
        role=UserRole.auditor,
        is_active=True,
    )
    team = _team(db_session)
    db_session.add(auditor)
    db_session.flush()

    customers = [
        Customer(name="Inbound Customer", email="inbound@example.test"),
        Customer(name="Outbound Customer", email="outbound@example.test"),
        Customer(name="Marker Customer", email="marker@example.test"),
        Customer(name="Voicemail Customer", email="voice-mail@example.test"),
    ]
    db_session.add_all(customers)
    db_session.flush()
    inbound_customer, outbound_customer, marker_customer, false_customer = customers

    inbound_ticket = Ticket(
        ticket_no=f"MAIL-IN-{_uid()}",
        title="Webhook ticket with inbound email",
        description="Inbound mailbox row establishes Email queue membership.",
        customer_id=inbound_customer.id,
        source=TicketSource.api,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.high,
        status=TicketStatus.in_progress,
        resolution_category=ResolutionCategory.none,
        conversation_state=ConversationState.human_owned,
        team_id=team.id,
    )
    outbound_ticket = Ticket(
        ticket_no=f"MAIL-OUT-{_uid()}",
        title="WhatsApp ticket with Email outbound failure",
        description="Failed SMTP reply establishes Email queue membership.",
        customer_id=outbound_customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.whatsapp,
        priority=TicketPriority.urgent,
        status=TicketStatus.waiting_internal,
        resolution_category=ResolutionCategory.none,
        conversation_state=ConversationState.ready_to_reply,
        team_id=team.id,
    )
    marker_ticket = Ticket(
        ticket_no=f"MAIL-MARK-{_uid()}",
        title="Native Email source ticket",
        description="Email marker ticket.",
        customer_id=marker_customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.email,
        priority=TicketPriority.medium,
        status=TicketStatus.in_progress,
        resolution_category=ResolutionCategory.none,
        conversation_state=ConversationState.human_owned,
        team_id=team.id,
    )
    false_ticket = Ticket(
        ticket_no=f"VOICE-{_uid()}",
        title="Voicemail should not be Email",
        description="Category contains mail as part of another word.",
        customer_id=false_customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.whatsapp,
        priority=TicketPriority.medium,
        status=TicketStatus.in_progress,
        category="voicemail",
        resolution_category=ResolutionCategory.none,
        conversation_state=ConversationState.human_owned,
        team_id=team.id,
    )
    db_session.add_all(
        [inbound_ticket, outbound_ticket, marker_ticket, false_ticket]
    )
    db_session.flush()

    inbound = TicketInboundEmailMessage(
        ticket_id=inbound_ticket.id,
        actor_id=admin.id,
        source="imap_poll",
        provider="imap",
        provider_message_id="provider-inbound-search",
        from_address="inbound@example.test",
        subject="Inbound mailbox projection proof",
        body="Customer replied from a real mailbox row.",
        body_preview="Customer replied from a real mailbox row.",
        mailbox_thread_id="<thread-inbound@example.test>",
        mailbox_message_id="<inbound-search@example.test>",
        mailbox_references="<previous@example.test>",
    )
    outbound = TicketOutboundMessage(
        ticket_id=outbound_ticket.id,
        channel=SourceChannel.email,
        status=MessageStatus.dead,
        subject="Failed SMTP projection proof",
        body="Failed outbound body",
        provider_status="smtp_550",
        error_message="Mailbox unavailable",
        created_by=admin.id,
        mailbox_thread_id="<thread-outbound@example.test>",
        mailbox_message_id="<outbound-dead@example.test>",
        mailbox_references="<thread-outbound@example.test>",
        failure_code="smtp_550",
        failure_reason="Mailbox unavailable",
    )
    db_session.add_all([inbound, outbound])
    db_session.flush()

    response = client.get("/api/email/queue", headers=_headers(admin))
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["source"] == "mailbox_projection"
    items = {item["ticket_id"]: item for item in payload["items"]}
    assert inbound_ticket.id in items
    assert outbound_ticket.id in items
    assert marker_ticket.id in items
    assert false_ticket.id not in items
    assert items[inbound_ticket.id]["queue_source"] == "inbound_email"
    assert items[inbound_ticket.id]["inbound_message_id"] == inbound.id
    assert items[outbound_ticket.id]["queue_source"] == "outbound_message"
    assert items[outbound_ticket.id]["outbound_message_id"] == outbound.id
    assert items[marker_ticket.id]["queue_source"] == "ticket_marker"

    searched = client.get(
        "/api/email/queue?q=inbound-search",
        headers=_headers(admin),
    )
    assert searched.status_code == 200, searched.text
    assert [item["ticket_id"] for item in searched.json()["items"]] == [
        inbound_ticket.id
    ]

    denied = client.get("/api/email/queue", headers=_headers(auditor))
    assert denied.status_code == 403
    assert denied.json()["detail"] == "email_queue_requires_outbound_capability"


def test_email_draft_send_and_timeline_audit_contract(
    client: TestClient,
    db_session,
):
    admin = _admin(db_session, user_id=9401)
    team = _team(db_session)
    ticket = _email_ticket(db_session, team=team)
    _smtp_account(db_session)
    attachment = _ticket_attachment(db_session, ticket, uploaded_by=admin.id)
    headers = _headers(admin)

    capabilities = client.get(
        f"/api/tickets/{ticket.id}/outbound/channels/capabilities",
        headers=headers,
    )
    assert capabilities.status_code == 200, capabilities.text
    email_capability = next(
        item
        for item in capabilities.json()["channels"]
        if item["channel"] == "email"
    )
    assert email_capability["supports_send"] is True
    assert email_capability["supports_attachments"] is True
    assert email_capability["missing"] == []

    draft = client.post(
        f"/api/tickets/{ticket.id}/outbound/draft",
        headers=headers,
        json={
            "channel": "email",
            "subject": "Draft subject",
            "body": "draft body",
            "attachment_ids": [attachment.id],
        },
    )
    assert draft.status_code == 200, draft.text
    draft_payload = draft.json()
    assert draft_payload["status"] == "draft"
    assert draft_payload["provider_status"] == "draft_saved"
    assert draft_payload["mailbox_thread_id"] == (
        f"<nexusdesk-ticket-{ticket.id}@nexusdesk.local>"
    )

    sent = client.post(
        f"/api/tickets/{ticket.id}/outbound/send",
        headers=headers,
        json={
            "channel": "email",
            "subject": "Send subject",
            "body": "send body",
            "attachment_ids": [attachment.id],
        },
    )
    assert sent.status_code == 200, sent.text
    send_payload = sent.json()
    assert send_payload["status"] == "pending"
    assert send_payload["provider_status"] == "queued"
    assert send_payload["delivery_semantics"] == "external_provider_send"
    assert send_payload["attachment_ids"] == [attachment.id]
    assert send_payload["mailbox_thread_id"] == draft_payload["mailbox_thread_id"]

    timeline = client.get(
        f"/api/tickets/{ticket.id}/timeline?limit=20",
        headers=headers,
    )
    assert timeline.status_code == 200, timeline.text
    items = timeline.json()["items"]
    outbound_items = [
        item for item in items if item.get("source_type") == "outbound_message"
    ]
    event_types = {
        item.get("event_type")
        for item in items
        if item.get("source_type") == "ticket_event"
    }
    assert any(
        item["subject"] == "Draft subject" and item["status"] == "draft"
        for item in outbound_items
    )
    assert any(
        item["subject"] == "Send subject" and item["status"] == "pending"
        for item in outbound_items
    )
    assert EventType.outbound_draft_saved.value in event_types
    assert EventType.outbound_queued.value in event_types


def test_email_inbound_ingest_merges_thread_and_writes_audit(
    client: TestClient,
    db_session,
):
    admin = _admin(db_session, user_id=9405)
    team = _team(db_session)
    ticket = _email_ticket(db_session, team=team)
    thread_id = f"<nexusdesk-ticket-{ticket.id}@nexusdesk.local>"
    outbound = TicketOutboundMessage(
        ticket_id=ticket.id,
        channel=SourceChannel.email,
        status=MessageStatus.pending,
        subject="Outbound thread anchor",
        body="Please reply with delivery proof.",
        provider_status="queued",
        provider_message_id="smtp-anchor-1",
        mailbox_thread_id=thread_id,
        mailbox_message_id=(
            f"<nexusdesk-ticket-{ticket.id}-outbound-anchor@nexusdesk.local>"
        ),
        mailbox_references=thread_id,
        created_by=admin.id,
    )
    db_session.add(outbound)
    db_session.flush()
    body = "Customer replied with new delivery evidence. " + (
        "audit-preview-boundary " * 40
    )
    payload = {
        "from_address": "Customer@Example.com",
        "from_name": "Email Customer",
        "to_address": "support@example.test",
        "subject": "Re: Outbound thread anchor",
        "body": body,
        "provider": "imap",
        "provider_message_id": "imap-provider-msg-1",
        "mailbox_message_id": "<customer-reply-1@example.test>",
        "mailbox_references": f"{outbound.mailbox_message_id} {thread_id}",
    }

    ingested = client.post(
        f"/api/tickets/{ticket.id}/email/inbound",
        headers=_headers(admin),
        json=payload,
    )
    assert ingested.status_code == 200, ingested.text
    ingested_payload = ingested.json()
    assert ingested_payload["created"] is True
    message = ingested_payload["message"]
    assert message["from_address"] == "customer@example.com"
    assert message["mailbox_thread_id"] == thread_id

    db_session.refresh(ticket)
    assert ticket.preferred_reply_channel == SourceChannel.email.value
    assert ticket.preferred_reply_contact == "customer@example.com"
    row = (
        db_session.query(TicketInboundEmailMessage)
        .filter(TicketInboundEmailMessage.ticket_id == ticket.id)
        .one()
    )
    event = db_session.get(TicketEvent, row.ticket_event_id)
    audit = db_session.get(AdminAuditLog, row.audit_id)
    assert event is not None and event.field_name == "email.inbound"
    assert audit is not None and audit.action == "email.inbound.ingested"
    audit_payload = json.loads(audit.new_value_json or "{}")
    assert audit_payload["body_preview"] != body
    assert len(audit_payload["body_preview"]) <= 500

    duplicate = client.post(
        f"/api/tickets/{ticket.id}/email/inbound",
        headers=_headers(admin),
        json=payload,
    )
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["created"] is False
    assert (
        db_session.query(TicketInboundEmailMessage)
        .filter(TicketInboundEmailMessage.ticket_id == ticket.id)
        .count()
        == 1
    )


def test_email_dead_outbound_requeue_contract(client: TestClient, db_session):
    admin = _admin(db_session, user_id=9403)
    team = _team(db_session)
    ticket = _email_ticket(db_session, team=team)
    row = TicketOutboundMessage(
        ticket_id=ticket.id,
        channel=SourceChannel.email,
        status=MessageStatus.dead,
        subject="Dead provider message",
        body="provider failed",
        provider_status="dead:smtp_timeout",
        provider_message_id="nexusdesk-outbound-dead-contract",
        mailbox_thread_id=f"<nexusdesk-ticket-{ticket.id}@nexusdesk.local>",
        mailbox_message_id=(
            "<nexusdesk-ticket-dead-contract-outbound@example.test>"
        ),
        mailbox_references=f"<nexusdesk-ticket-{ticket.id}@nexusdesk.local>",
        retry_count=3,
        max_retries=3,
        failure_code="smtp_timeout",
        failure_reason="SMTP timed out",
        created_by=admin.id,
    )
    db_session.add(row)
    db_session.commit()

    timeline = client.get(
        f"/api/tickets/{ticket.id}/timeline?limit=20",
        headers=_headers(admin),
    )
    assert timeline.status_code == 200, timeline.text
    outbound = next(
        item
        for item in timeline.json()["items"]
        if item.get("source_type") == "outbound_message"
    )
    assert outbound["status"] == "dead"
    assert outbound["failure_code"] == "smtp_timeout"

    requeued = client.post(
        f"/api/admin/outbound/{row.id}/requeue",
        headers=_headers(admin),
    )
    assert requeued.status_code == 200, requeued.text
    db_session.refresh(row)
    assert row.status == MessageStatus.pending
    assert row.provider_status == "requeued_by_admin"
    assert row.retry_count == 0
    assert row.failure_code is None
    assert row.failure_reason is None


def test_email_delivery_receipt_updates_timeline_and_audit(
    client: TestClient,
    db_session,
):
    admin = _admin(db_session, user_id=9406)
    team = _team(db_session)
    ticket = _email_ticket(db_session, team=team)
    row = TicketOutboundMessage(
        ticket_id=ticket.id,
        channel=SourceChannel.email,
        status=MessageStatus.pending,
        subject="Receipt target",
        body="receipt target",
        provider_status="queued",
        provider_message_id="smtp-provider-target",
        mailbox_thread_id=f"<nexusdesk-ticket-{ticket.id}@nexusdesk.local>",
        mailbox_message_id=(
            f"<nexusdesk-ticket-{ticket.id}-outbound-receipt@nexusdesk.local>"
        ),
        mailbox_references=f"<nexusdesk-ticket-{ticket.id}@nexusdesk.local>",
        created_by=admin.id,
    )
    db_session.add(row)
    db_session.flush()
    payload = {
        "delivery_status": "delivered",
        "provider": "smtp-webhook",
        "provider_event_type": "delivered",
        "provider_event_id": "receipt-event-1",
        "provider_status": "provider_delivered",
        "detail": "Provider delivered the message.",
        "raw_payload": {"message": "safe", "api_key": "secret-value"},
    }

    receipt = client.post(
        f"/api/tickets/{ticket.id}/email/outbound/{row.id}/delivery-receipt",
        headers=_headers(admin),
        json=payload,
    )
    assert receipt.status_code == 200, receipt.text
    result = receipt.json()
    assert result["created"] is True
    assert result["status"] == "sent"
    assert result["delivery_status"] == "delivered"
    db_session.refresh(row)
    assert row.status == MessageStatus.sent
    assert row.provider_status == "provider_delivered"
    assert json.loads(row.delivery_payload_json or "{}")["api_key"] == "[redacted]"

    audit = db_session.get(AdminAuditLog, result["audit_id"])
    assert audit is not None and audit.action == "email.delivery_receipt.ingested"
    duplicate = client.post(
        f"/api/tickets/{ticket.id}/email/outbound/{row.id}/delivery-receipt",
        headers=_headers(admin),
        json=payload,
    )
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["created"] is False


def test_formal_webcall_uses_scope_offer_handoff_and_durable_commands(
    client: TestClient,
    db_session,
):
    admin = _admin(db_session, user_id=9404)
    conversation, ticket, visitor_token = _init_webcall(
        client,
        db_session,
        admin=admin,
        with_ticket=True,
    )
    assert ticket is not None

    created = client.post(
        f"/api/webchat/conversations/{conversation.public_id}/voice/sessions",
        headers={"X-Webchat-Visitor-Token": visitor_token},
        json={},
    )
    assert created.status_code == 200, created.text
    voice_session_id = created.json()["voice_session_id"]
    assert created.json()["voice_offer"] is not None

    queue = client.get(
        "/api/webchat/admin/voice/sessions?status=incoming&limit=20",
        headers=_headers(admin),
    )
    assert queue.status_code == 200, queue.text
    item = next(
        row
        for row in queue.json()["items"]
        if row["voice_session_id"] == voice_session_id
    )
    assert item["ticket_id"] == ticket.id
    assert item["visitor_label"] == "Voice Visitor"
    assert "participant_token" not in item

    accepted = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/accept",
        headers=_headers(admin),
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["accepted_by_user_id"] == admin.id

    hold = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/actions",
        headers=_headers(admin),
        json={
            "action_type": "hold",
            "note": "Hold requested from the channel workbench.",
        },
    )
    assert hold.status_code == 200, hold.text
    action = hold.json()["action"]
    assert action["status"] == "requested"
    assert action["provider_status"] == "pending"
    assert "provider_adapter_pending" not in hold.text

    db_session.expire_all()
    session = (
        db_session.query(WebchatVoiceSession)
        .filter(WebchatVoiceSession.public_id == voice_session_id)
        .one()
    )
    handoff = db_session.get(WebchatHandoffRequest, session.handoff_request_id)
    offer = (
        db_session.query(VoiceRoutingOffer)
        .filter(VoiceRoutingOffer.voice_session_id == session.id)
        .one()
    )
    conversation = db_session.get(WebchatConversation, conversation.id)
    command = (
        db_session.query(WebchatVoiceSessionAction)
        .filter(WebchatVoiceSessionAction.public_id == action["id"])
        .one()
    )
    assert handoff is not None and handoff.status == "accepted"
    assert handoff.assigned_agent_id == admin.id
    assert conversation is not None and conversation.active_agent_id == admin.id
    assert offer.status == "accepted"
    assert not hasattr(session, "accepted_by_user_id")
    assert command.status == "requested"


def test_customer_hangup_closes_formal_webcall_without_duplicate_owner(
    client: TestClient,
    db_session,
):
    admin = _admin(db_session, user_id=9402)
    conversation, ticket, visitor_token = _init_webcall(
        client,
        db_session,
        admin=admin,
        with_ticket=True,
    )
    assert ticket is not None
    created = client.post(
        f"/api/webchat/conversations/{conversation.public_id}/voice/sessions",
        headers={"X-Webchat-Visitor-Token": visitor_token},
        json={},
    )
    assert created.status_code == 200, created.text
    voice_session_id = created.json()["voice_session_id"]
    accepted = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/accept",
        headers=_headers(admin),
    )
    assert accepted.status_code == 200, accepted.text

    ended = client.post(
        (
            f"/api/webchat/conversations/{conversation.public_id}/voice/"
            f"{voice_session_id}/end"
        ),
        headers={"X-Webchat-Visitor-Token": visitor_token},
    )
    assert ended.status_code == 200, ended.text
    assert ended.json()["status"] == "ended"

    db_session.expire_all()
    session = (
        db_session.query(WebchatVoiceSession)
        .filter(WebchatVoiceSession.public_id == voice_session_id)
        .one()
    )
    handoff = db_session.get(WebchatHandoffRequest, session.handoff_request_id)
    canonical_conversation = db_session.get(WebchatConversation, conversation.id)
    assert session.ended_at is not None
    assert session.ended_by_user_id is None
    assert handoff is not None and handoff.status == "closed"
    assert canonical_conversation is not None
    assert canonical_conversation.active_agent_id is None

    timeline = client.get(
        f"/api/tickets/{ticket.id}/timeline?limit=40",
        headers=_headers(admin),
    )
    assert timeline.status_code == 200, timeline.text
    event_types = {
        item.get("event_type")
        for item in timeline.json()["items"]
        if item.get("source_type") == "webchat_event"
    }
    assert "voice.session.created" in event_types
    assert "voice.session.active" in event_types
    assert "voice.session.ended" in event_types
    assert "handoff.accepted" in event_types
