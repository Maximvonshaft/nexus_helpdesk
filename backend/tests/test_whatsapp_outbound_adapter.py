from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/helpdesk_suite_whatsapp_outbound_adapter.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.db import Base  # noqa: E402
from app.enums import MessageStatus, ResolutionCategory, SourceChannel, TicketPriority, TicketSource, TicketStatus  # noqa: E402
from app.models import ChannelAccount, Customer, Team, Ticket, TicketOutboundMessage  # noqa: E402
from app.services import message_dispatch  # noqa: E402
from app.services.outbound_adapters.whatsapp import dispatch_whatsapp_outbound, resolve_whatsapp_outbound_route  # noqa: E402
from app.utils.time import utc_now  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "whatsapp_outbound_adapter.db"
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


def _ticket(db_session, *, contact="+15550123456") -> Ticket:
    team = Team(name=f"Ops-{_uid()}", team_type="support")
    customer = Customer(name="Alice", phone=contact, email="alice@example.test")
    db_session.add_all([team, customer])
    db_session.flush()
    ticket = Ticket(
        ticket_no=f"T-{_uid()}",
        title="Customer message",
        description="Customer message",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.whatsapp,
        priority=TicketPriority.medium,
        status=TicketStatus.pending_assignment,
        resolution_category=ResolutionCategory.none,
        team_id=team.id,
        source_chat_id=contact,
        preferred_reply_channel=SourceChannel.whatsapp.value,
        preferred_reply_contact=contact,
    )
    db_session.add(ticket)
    db_session.flush()
    return ticket


def _message(db_session, ticket: Ticket, *, body="hello") -> TicketOutboundMessage:
    row = TicketOutboundMessage(
        ticket_id=ticket.id,
        channel=SourceChannel.whatsapp,
        status=MessageStatus.processing,
        body=body,
        provider_status="queued",
        max_retries=3,
        locked_by="worker-test",
    )
    db_session.add(row)
    db_session.flush()
    row.ticket = ticket
    return row


def _add_whatsapp_account(db_session, *, account_id="wa-main") -> ChannelAccount:
    row = ChannelAccount(provider="whatsapp", account_id=account_id, display_name="WhatsApp Main", is_active=True, priority=10)
    db_session.add(row)
    db_session.flush()
    return row


def test_resolve_whatsapp_route_requires_active_whatsapp_account(db_session):
    ticket = _ticket(db_session)
    message = _message(db_session, ticket)

    with pytest.raises(ValueError, match="missing_whatsapp_channel_account"):
        resolve_whatsapp_outbound_route(db_session, message=message, ticket=ticket)


def test_resolve_whatsapp_route_rejects_missing_target(db_session):
    ticket = _ticket(db_session, contact="")
    ticket.source_chat_id = None
    ticket.preferred_reply_contact = None
    ticket.customer.phone = None
    _add_whatsapp_account(db_session)
    message = _message(db_session, ticket)

    with pytest.raises(ValueError, match="missing_whatsapp_target"):
        resolve_whatsapp_outbound_route(db_session, message=message, ticket=ticket)


def test_dispatch_whatsapp_outbound_builds_bridge_payload(db_session):
    ticket = _ticket(db_session, contact="+15550123456")
    _add_whatsapp_account(db_session, account_id="wa-main")
    message = _message(db_session, ticket, body="hello customer")
    calls = []

    def fake_dispatch(**kwargs):
        calls.append(kwargs)
        return MessageStatus.sent, "sent_via_fake_whatsapp_bridge", utc_now()

    status_value, provider_status, sent_at, route = dispatch_whatsapp_outbound(
        db_session,
        message=message,
        ticket=ticket,
        idempotency_key="idem-1",
        dispatch_fn=fake_dispatch,
    )

    assert status_value == MessageStatus.sent
    assert provider_status == "sent_via_fake_whatsapp_bridge"
    assert sent_at is not None
    assert calls == [{
        "channel": "whatsapp",
        "target": "+15550123456",
        "body": "hello customer",
        "account_id": "wa-main",
        "thread_id": None,
        "session_key": None,
    }]
    assert route["adapter"] == "whatsapp_openclaw_bridge"
    assert route["account_id"] == "wa-main"
    assert route["target"] == "+15550123456"
    assert route["idempotency_key"] == "idem-1"


def test_process_whatsapp_message_missing_account_never_calls_provider(db_session, monkeypatch):
    ticket = _ticket(db_session, contact="+15550123456")
    message = _message(db_session, ticket)
    called = {"provider": False}

    monkeypatch.setattr(message_dispatch.settings, "enable_outbound_dispatch", True)
    monkeypatch.setattr(message_dispatch.settings, "outbound_provider", "openclaw")
    monkeypatch.setattr(message_dispatch, "log_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(message_dispatch, "_enforce_outbound_safety", lambda *args, **kwargs: True)
    monkeypatch.setattr(message_dispatch, "dispatch_via_openclaw_cli", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("cli fallback must not run")))

    def fake_whatsapp_dispatch(*args, **kwargs):
        called["provider"] = True
        raise ValueError("missing_whatsapp_channel_account")

    monkeypatch.setattr(message_dispatch, "dispatch_whatsapp_outbound", fake_whatsapp_dispatch)

    processed = message_dispatch.process_outbound_message(db_session, message)

    assert called["provider"] is True
    assert processed.status == MessageStatus.pending
    assert processed.failure_code == "retryable_dispatch_error"
    assert processed.failure_reason == "missing_whatsapp_channel_account"


def test_process_whatsapp_message_success_sets_sent_and_waiting_customer(db_session, monkeypatch):
    ticket = _ticket(db_session, contact="+15550123456")
    message = _message(db_session, ticket, body="resolved update")

    monkeypatch.setattr(message_dispatch.settings, "enable_outbound_dispatch", True)
    monkeypatch.setattr(message_dispatch.settings, "outbound_provider", "openclaw")
    monkeypatch.setattr(message_dispatch, "log_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(message_dispatch, "_enforce_outbound_safety", lambda *args, **kwargs: True)

    def fake_whatsapp_dispatch(db, *, message, ticket, idempotency_key):
        return MessageStatus.sent, "sent_via_fake_whatsapp_bridge", utc_now(), {
            "channel": "whatsapp",
            "target": "+15550123456",
            "account_id": "wa-main",
            "session_key": None,
            "thread_id": None,
            "idempotency_key": idempotency_key,
            "adapter": "whatsapp_openclaw_bridge",
        }

    monkeypatch.setattr(message_dispatch, "dispatch_whatsapp_outbound", fake_whatsapp_dispatch)

    processed = message_dispatch.process_outbound_message(db_session, message)

    assert processed.status == MessageStatus.sent
    assert processed.provider_status == "sent_via_fake_whatsapp_bridge"
    assert processed.sent_at is not None
    assert ticket.conversation_state.value == "waiting_customer"
