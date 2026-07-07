from __future__ import annotations

import os
import smtplib
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/helpdesk_suite_email_outbound_runtime.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.db import Base  # noqa: E402
from app.enums import ConversationState, MessageStatus, NoteVisibility, ResolutionCategory, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.models import Customer, OutboundEmailAccount, Team, Ticket, TicketAttachment, TicketOutboundMessage, User  # noqa: E402
from app.schemas import OutboundSendRequest  # noqa: E402
from app.services import message_dispatch  # noqa: E402
from app.services.outbound_adapters.email import dispatch_email_outbound, send_outbound_email_test  # noqa: E402
from app.services.secret_crypto import SecretCryptoService  # noqa: E402
from app.services.ticket_service import send_outbound_message  # noqa: E402
from app.utils.time import utc_now  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "email_outbound_runtime.db"
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


def _uid() -> str:
    return uuid.uuid4().hex[:10]


def _admin(db_session) -> User:
    row = User(
        username=f"admin-{_uid()}",
        display_name="Admin",
        email=f"admin-{_uid()}@example.test",
        password_hash="x",
        role=UserRole.admin,
        is_active=True,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _ticket(db_session, *, contact="alice@example.com", title="Shipment update") -> Ticket:
    team = Team(name=f"Ops-{_uid()}", team_type="support")
    customer = Customer(name="Alice", phone="+15550123456", email="fallback@example.com")
    db_session.add_all([team, customer])
    db_session.flush()
    ticket = Ticket(
        ticket_no=f"T-{_uid()}",
        title=title,
        description="Customer message",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.email,
        priority=TicketPriority.medium,
        status=TicketStatus.pending_assignment,
        resolution_category=ResolutionCategory.none,
        conversation_state=ConversationState.human_owned,
        team_id=team.id,
        source_chat_id=contact,
        preferred_reply_channel=SourceChannel.email.value,
        preferred_reply_contact=contact,
    )
    db_session.add(ticket)
    db_session.flush()
    return ticket


def _email_account(db_session, *, market_id=None) -> OutboundEmailAccount:
    row = OutboundEmailAccount(
        display_name="Support SMTP",
        host="smtp.example.test",
        port=587,
        username="support@example.com",
        password_encrypted=SecretCryptoService.outbound_email().encrypt("smtp-secret"),
        from_address="support@example.com",
        reply_to="replies@example.com",
        security_mode="starttls",
        market_id=market_id,
        is_active=True,
        priority=10,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _message(db_session, ticket: Ticket, *, subject="Order update", body="hello customer") -> TicketOutboundMessage:
    row = TicketOutboundMessage(
        ticket_id=ticket.id,
        channel=SourceChannel.email,
        status=MessageStatus.processing,
        subject=subject,
        body=body,
        provider_status="queued",
        max_retries=3,
        locked_by="worker-test",
    )
    db_session.add(row)
    db_session.flush()
    row.ticket = ticket
    return row


def _attachment(db_session, ticket: Ticket, file_path: Path, *, visibility=NoteVisibility.external) -> TicketAttachment:
    row = TicketAttachment(
        ticket_id=ticket.id,
        file_name=file_path.name,
        file_path=str(file_path),
        mime_type="text/plain",
        file_size=file_path.stat().st_size,
        visibility=visibility,
    )
    db_session.add(row)
    db_session.flush()
    return row


class FakeSMTP:
    def __init__(self):
        self.login_calls = []
        self.messages = []
        self.closed = False

    def login(self, username, password):
        self.login_calls.append((username, password))

    def send_message(self, message):
        self.messages.append(message)
        return {}

    def quit(self):
        self.closed = True

    def close(self):
        self.closed = True


def test_dispatch_email_outbound_builds_smtp_message_and_redacted_route(db_session):
    ticket = _ticket(db_session, contact="Alice@Example.com")
    _email_account(db_session)
    message = _message(db_session, ticket, subject="  Order update\n")
    fake_client = FakeSMTP()
    factory_calls = []

    def fake_factory(**kwargs):
        factory_calls.append(kwargs)
        return fake_client

    status_value, provider_status, sent_at, route = dispatch_email_outbound(
        db_session,
        message=message,
        ticket=ticket,
        idempotency_key="idem-1",
        smtp_factory=fake_factory,
    )

    assert status_value == MessageStatus.sent
    assert provider_status == "smtp_sent"
    assert sent_at is not None
    assert factory_calls[0]["host"] == "smtp.example.test"
    assert factory_calls[0]["security_mode"] == "starttls"
    assert fake_client.login_calls == [("support@example.com", "smtp-secret")]
    assert fake_client.messages[0]["To"] == "alice@example.com"
    assert fake_client.messages[0]["Subject"] == "Order update"
    assert fake_client.messages[0]["Message-ID"] == message.mailbox_message_id
    assert fake_client.messages[0]["In-Reply-To"] == message.mailbox_thread_id
    assert fake_client.messages[0]["References"] == message.mailbox_references
    assert fake_client.messages[0]["X-NexusDesk-Mailbox-Thread-ID"] == message.mailbox_thread_id
    assert fake_client.messages[0]["X-NexusDesk-Idempotency-Key"] == "idem-1"
    assert fake_client.closed is True
    assert message.mailbox_thread_id == f"<nexusdesk-ticket-{ticket.id}@nexusdesk.local>"
    assert message.mailbox_message_id == f"<nexusdesk-ticket-{ticket.id}-outbound-{message.id}@nexusdesk.local>"
    assert message.mailbox_references == message.mailbox_thread_id
    assert route["adapter"] == "smtp"
    assert route["account_id"] is not None
    assert route["to_address"] == "a***@example.com"
    assert route["from_address"] == "s***@example.com"
    assert route["mailbox_thread_id"] == message.mailbox_thread_id
    assert route["mailbox_message_id"] == message.mailbox_message_id
    assert route["mailbox_references"] == message.mailbox_references
    assert "password" not in route
    assert "username" not in route


def test_dispatch_email_outbound_attaches_ticket_files(db_session, tmp_path):
    ticket = _ticket(db_session, contact="Alice@Example.com")
    _email_account(db_session)
    attachment_path = tmp_path / "proof.txt"
    attachment_path.write_text("delivery proof", encoding="utf-8")
    attachment = _attachment(db_session, ticket, attachment_path)
    admin = _admin(db_session)
    message = send_outbound_message(
        db_session,
        ticket.id,
        OutboundSendRequest(channel=SourceChannel.email, subject="Attached proof", body="hello", attachment_ids=[attachment.id]),
        admin,
    )
    fake_client = FakeSMTP()

    status_value, provider_status, sent_at, route = dispatch_email_outbound(
        db_session,
        message=message,
        ticket=ticket,
        idempotency_key="idem-attachment",
        smtp_factory=lambda **kwargs: fake_client,
    )

    assert status_value == MessageStatus.sent
    assert provider_status == "smtp_sent"
    assert sent_at is not None
    assert fake_client.messages[0]["Message-ID"] == message.mailbox_message_id
    assert fake_client.messages[0]["In-Reply-To"] == message.mailbox_thread_id
    assert fake_client.messages[0]["References"] == message.mailbox_references
    attachments = list(fake_client.messages[0].iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "proof.txt"
    assert attachments[0].get_payload(decode=True) == b"delivery proof"
    assert route["attachment_count"] == 1
    assert route["attachment_filenames"] == ["proof.txt"]
    assert route["mailbox_message_id"] == message.mailbox_message_id


def test_dispatch_email_outbound_maps_smtp_auth_failure(db_session):
    ticket = _ticket(db_session)
    _email_account(db_session)
    message = _message(db_session, ticket)

    class AuthFailSMTP(FakeSMTP):
        def login(self, username, password):
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    status_value, provider_status, sent_at, route = dispatch_email_outbound(
        db_session,
        message=message,
        ticket=ticket,
        idempotency_key="idem-auth",
        smtp_factory=lambda **kwargs: AuthFailSMTP(),
    )

    assert status_value == MessageStatus.failed
    assert provider_status == "smtp_auth_failed"
    assert sent_at is None
    assert route["failure_code"] == "smtp_auth_failed"
    assert "bad credentials" in route["error"]


def test_send_outbound_email_test_updates_fake_smtp(db_session):
    account = _email_account(db_session)
    fake_client = FakeSMTP()

    status_value, provider_status, sent_at, route = send_outbound_email_test(
        account,
        to_address="ops@example.com",
        subject="Probe",
        body="hello",
        smtp_factory=lambda **kwargs: fake_client,
    )

    assert status_value == MessageStatus.sent
    assert provider_status == "smtp_sent"
    assert sent_at is not None
    assert fake_client.messages[0]["Subject"] == "Probe"
    assert fake_client.messages[0].get_content().strip() == "hello"
    assert route["source"] == "admin_test_send"


@pytest.mark.parametrize("security_mode", ["starttls", "ssl", "plain"])
def test_send_outbound_email_test_accepts_supported_security_modes(db_session, security_mode):
    account = _email_account(db_session)
    account.security_mode = security_mode
    factory_calls = []

    status_value, provider_status, sent_at, _route = send_outbound_email_test(
        account,
        to_address="ops@example.com",
        smtp_factory=lambda **kwargs: factory_calls.append(kwargs) or FakeSMTP(),
    )

    assert status_value == MessageStatus.sent
    assert provider_status == "smtp_sent"
    assert sent_at is not None
    assert factory_calls[0]["security_mode"] == security_mode


def test_process_email_message_uses_smtp_adapter_and_marks_sent(db_session, monkeypatch):
    ticket = _ticket(db_session)
    message = _message(db_session, ticket)

    monkeypatch.setattr(message_dispatch.settings, "enable_outbound_dispatch", True)
    monkeypatch.setattr(message_dispatch.settings, "outbound_provider", "email")
    monkeypatch.setattr(message_dispatch.settings, "allow_legacy_originless_outbound", True)
    monkeypatch.setattr(message_dispatch, "log_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(message_dispatch, "_enforce_outbound_safety", lambda *args, **kwargs: True)
    monkeypatch.setattr(message_dispatch, "dispatch_via_external_channel_bridge", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("legacy bridge alias must not run for email")))
    monkeypatch.setattr(message_dispatch, "dispatch_via_external_channel_cli", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("legacy CLI alias must not run for email")))

    def fake_email_dispatch(db, *, message, ticket, idempotency_key):
        return MessageStatus.sent, "smtp_sent", utc_now(), {
            "channel": "email",
            "adapter": "smtp",
            "idempotency_key": idempotency_key,
        }

    monkeypatch.setattr(message_dispatch, "dispatch_email_outbound", fake_email_dispatch)

    processed = message_dispatch.process_outbound_message(db_session, message)

    assert processed.status == MessageStatus.sent
    assert processed.provider_status == "smtp_sent"
    assert processed.sent_at is not None
    assert ticket.conversation_state == ConversationState.waiting_customer


def test_process_email_failure_preserves_smtp_failure_code(db_session, monkeypatch):
    ticket = _ticket(db_session)
    message = _message(db_session, ticket)

    monkeypatch.setattr(message_dispatch.settings, "enable_outbound_dispatch", True)
    monkeypatch.setattr(message_dispatch.settings, "outbound_provider", "email")
    monkeypatch.setattr(message_dispatch.settings, "allow_legacy_originless_outbound", True)
    monkeypatch.setattr(message_dispatch, "log_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(message_dispatch, "_enforce_outbound_safety", lambda *args, **kwargs: True)

    def fake_email_dispatch(db, *, message, ticket, idempotency_key):
        return MessageStatus.failed, "smtp_rate_limited", None, {
            "channel": "email",
            "adapter": "smtp",
            "failure_code": "smtp_rate_limited",
            "error": "421 rate limited",
        }

    monkeypatch.setattr(message_dispatch, "dispatch_email_outbound", fake_email_dispatch)

    processed = message_dispatch.process_outbound_message(db_session, message)

    assert processed.status == MessageStatus.pending
    assert processed.failure_code == "smtp_rate_limited"
    assert processed.failure_reason == "421 rate limited"
    assert processed.provider_status == "retry_scheduled:1m:smtp_rate_limited"


def test_ticket_send_email_persists_explicit_or_default_subject(db_session):
    admin = _admin(db_session)
    ticket = _ticket(db_session, title="Ticket title subject")

    explicit = send_outbound_message(
        db_session,
        ticket.id,
        OutboundSendRequest(channel=SourceChannel.email, subject="Explicit subject", body="hello"),
        admin,
    )
    fallback = send_outbound_message(
        db_session,
        ticket.id,
        OutboundSendRequest(channel=SourceChannel.email, body="hello"),
        admin,
    )

    assert explicit.subject == "Explicit subject"
    assert fallback.subject == "Ticket title subject"


def test_ticket_send_email_links_only_external_ticket_attachments(db_session, tmp_path):
    admin = _admin(db_session)
    ticket = _ticket(db_session, title="Ticket title subject")
    file_path = tmp_path / "customer-proof.txt"
    file_path.write_text("customer proof", encoding="utf-8")
    attachment = _attachment(db_session, ticket, file_path)
    internal_path = tmp_path / "internal-note.txt"
    internal_path.write_text("internal only", encoding="utf-8")
    internal_attachment = _attachment(db_session, ticket, internal_path, visibility=NoteVisibility.internal)

    message = send_outbound_message(
        db_session,
        ticket.id,
        OutboundSendRequest(channel=SourceChannel.email, subject="With attachment", body="hello", attachment_ids=[attachment.id, attachment.id]),
        admin,
    )

    assert [item.id for item in message.attachments] == [attachment.id]

    with pytest.raises(HTTPException) as exc:
        send_outbound_message(
            db_session,
            ticket.id,
            OutboundSendRequest(channel=SourceChannel.email, subject="Internal", body="hello", attachment_ids=[internal_attachment.id]),
            admin,
        )
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as channel_exc:
        send_outbound_message(
            db_session,
            ticket.id,
            OutboundSendRequest(channel=SourceChannel.web_chat, body="hello", attachment_ids=[attachment.id]),
            admin,
        )
    assert channel_exc.value.status_code == 400
