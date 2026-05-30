from __future__ import annotations

import email.utils
import os
import sys
import uuid
from email.message import EmailMessage
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")
os.environ.setdefault("EMAIL_MAILBOX_SYNC_ENABLED", "true")
os.environ.setdefault("EMAIL_MAILBOX_SYNC_INTERVAL_SECONDS", "60")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models as _models  # noqa: E402,F401
from app.auth_service import create_access_token  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.enums import ConversationState, EventType, JobStatus, ResolutionCategory, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AdminAuditLog, BackgroundJob, Customer, OutboundEmailAccount, Team, Ticket, TicketEvent, TicketInboundEmailMessage, User  # noqa: E402
from app.services import background_jobs, email_mailbox_polling_service  # noqa: E402
from app.services.secret_crypto import SecretCryptoService  # noqa: E402
from app.settings import get_settings  # noqa: E402
from app.utils.normalize import normalize_email  # noqa: E402
from app.utils.time import utc_now  # noqa: E402


@pytest.fixture()
def db_session(tmp_path, monkeypatch):
    monkeypatch.setenv("EMAIL_MAILBOX_SYNC_ENABLED", "true")
    monkeypatch.setenv("EMAIL_MAILBOX_SYNC_INTERVAL_SECONDS", "60")
    monkeypatch.setenv("EMAIL_MAILBOX_SYNC_BATCH_SIZE", "20")
    get_settings.cache_clear()
    monkeypatch.setattr(background_jobs, "settings", get_settings())

    db_file = tmp_path / "email_mailbox_polling.db"
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


def _admin(db_session, *, user_id: int = 9501) -> User:
    row = User(
        id=user_id,
        username=f"mailbox-poll-admin-{_uid()}",
        display_name="Mailbox Poll Admin",
        email=f"mailbox-poll-admin-{_uid()}@example.test",
        password_hash="test",
        role=UserRole.admin,
        is_active=True,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _auditor(db_session) -> User:
    row = User(
        username=f"mailbox-poll-auditor-{_uid()}",
        display_name="Mailbox Poll Auditor",
        email=f"mailbox-poll-auditor-{_uid()}@example.test",
        password_hash="test",
        role=UserRole.auditor,
        is_active=True,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def _ticket(db_session, *, team: Team) -> Ticket:
    customer = Customer(name="IMAP Customer", email="customer@example.test", email_normalized=normalize_email("customer@example.test"))
    db_session.add(customer)
    db_session.flush()
    ticket = Ticket(
        ticket_no=f"IMAP-{_uid()}",
        title="IMAP customer reply",
        description="Customer will reply through a mailbox.",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.email,
        priority=TicketPriority.medium,
        status=TicketStatus.in_progress,
        resolution_category=ResolutionCategory.none,
        conversation_state=ConversationState.human_owned,
        team_id=team.id,
        source_chat_id="customer@example.test",
        preferred_reply_channel=SourceChannel.email.value,
        preferred_reply_contact="customer@example.test",
    )
    db_session.add(ticket)
    db_session.flush()
    return ticket


def _imap_account(db_session, *, admin: User) -> OutboundEmailAccount:
    row = OutboundEmailAccount(
        display_name="Support IMAP",
        host="smtp.example.test",
        port=587,
        username="support@example.test",
        password_encrypted=SecretCryptoService.outbound_email().encrypt("smtp-secret"),
        from_address="support@example.test",
        reply_to="reply@example.test",
        security_mode="starttls",
        inbound_enabled=True,
        imap_host="imap.example.test",
        imap_port=993,
        imap_username="support@example.test",
        imap_password_encrypted=SecretCryptoService.outbound_email().encrypt("imap-secret"),
        imap_security_mode="ssl",
        imap_mailbox="INBOX",
        is_active=True,
        priority=10,
        created_by=admin.id,
        updated_by=admin.id,
    )
    db_session.add(row)
    db_session.flush()
    return row


class FakeMailbox:
    def __init__(self, raw_message: bytes) -> None:
        self.raw_message = raw_message
        self.selected: str | None = None
        self.logged_out = False

    def select(self, mailbox: str):
        self.selected = mailbox
        return "OK", [b""]

    def uid(self, command: str, *args):
        if command == "search":
            return "OK", [b"101"]
        if command == "fetch":
            return "OK", [(b"101 (RFC822)", self.raw_message)]
        raise AssertionError(f"unexpected IMAP command: {command}")

    def logout(self):
        self.logged_out = True
        return "OK", [b""]


def _raw_reply(ticket: Ticket) -> bytes:
    message = EmailMessage()
    message["From"] = "IMAP Customer <customer@example.test>"
    message["To"] = "Support <support@example.test>"
    message["Subject"] = "Re: IMAP customer reply"
    message["Message-ID"] = "<imap-reply-101@example.test>"
    message["References"] = f"<nexusdesk-ticket-{ticket.id}@nexusdesk.local>"
    message["Date"] = email.utils.format_datetime(utc_now())
    message.set_content("Customer replied through the IMAP polling daemon with delivery evidence.")
    return message.as_bytes()


def test_email_mailbox_daemon_dispatch_ingests_imap_and_writes_timeline_audit(client: TestClient, db_session, monkeypatch):
    admin = _admin(db_session)
    auditor = _auditor(db_session)
    team = Team(name=f"Mailbox {_uid()}", team_type="support")
    db_session.add(team)
    db_session.flush()
    ticket = _ticket(db_session, team=team)
    account = _imap_account(db_session, admin=admin)
    fake_mailbox = FakeMailbox(_raw_reply(ticket))
    monkeypatch.setattr(email_mailbox_polling_service, "_connect_imap", lambda row: fake_mailbox)

    status_response = client.get("/api/email/mailbox-sync/status", headers=_headers(admin))
    assert status_response.status_code == 200, status_response.text
    status_payload = status_response.json()
    assert status_payload["daemon_enabled"] is True
    assert status_payload["enabled_accounts"] == 1
    assert status_payload["configured_accounts"] == 1
    assert status_payload["accounts"][0]["configured"] is True

    denied = client.get("/api/email/mailbox-sync/status", headers=_headers(auditor))
    assert denied.status_code == 403

    processed = background_jobs.dispatch_pending_background_jobs(db_session, limit=5, worker_id="mailbox-test")
    assert [job.job_type for job in processed] == [background_jobs.EMAIL_MAILBOX_SYNC_JOB]
    assert processed[0].status == JobStatus.done
    assert fake_mailbox.selected == "INBOX"
    assert fake_mailbox.logged_out is True

    db_session.refresh(account)
    assert account.imap_sync_cursor == "101"
    assert account.imap_last_status == "ok"
    assert account.imap_last_error is None

    inbound = db_session.query(TicketInboundEmailMessage).filter(TicketInboundEmailMessage.ticket_id == ticket.id).one()
    assert inbound.source == "imap_poll"
    assert inbound.provider == "imap"
    assert inbound.provider_message_id == f"imap:{account.id}:101"
    assert inbound.mailbox_message_id == "<imap-reply-101@example.test>"
    assert inbound.mailbox_thread_id == f"<nexusdesk-ticket-{ticket.id}@nexusdesk.local>"
    assert inbound.ticket_event_id is not None
    assert inbound.audit_id is not None

    event = db_session.query(TicketEvent).filter(TicketEvent.id == inbound.ticket_event_id).one()
    assert event.event_type == EventType.comment_added
    assert event.field_name == "email.inbound"
    audit = db_session.query(AdminAuditLog).filter(AdminAuditLog.id == inbound.audit_id).one()
    assert audit.action == "email.inbound.ingested"

    timeline = client.get(f"/api/tickets/{ticket.id}/timeline?limit=20", headers=_headers(admin))
    assert timeline.status_code == 200, timeline.text
    timeline_inbound = next(item for item in timeline.json()["items"] if item.get("source_type") == "inbound_email")
    assert timeline_inbound["source_id"] == inbound.id
    assert timeline_inbound["provider"] == "imap"
    assert timeline_inbound["mailbox_message_id"] == "<imap-reply-101@example.test>"

    manual = client.post("/api/email/mailbox-sync/enqueue", headers=_headers(admin), json={"account_id": account.id})
    assert manual.status_code == 200, manual.text
    assert manual.json()["enqueued"] == 1
    pending = db_session.query(BackgroundJob).filter(BackgroundJob.job_type == background_jobs.EMAIL_MAILBOX_SYNC_JOB, BackgroundJob.status == JobStatus.pending).one()
    assert pending.payload_json == f'{{"account_id": {account.id}}}'
