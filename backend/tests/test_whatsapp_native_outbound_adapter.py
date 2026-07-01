from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/helpdesk_suite_whatsapp_native_outbound_adapter.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.db import Base  # noqa: E402
from app.enums import MessageStatus, ResolutionCategory, SourceChannel, TicketPriority, TicketSource, TicketStatus  # noqa: E402
from app.models import ChannelAccount, Customer, Team, Ticket, TicketOutboundMessage  # noqa: E402
from app.services import message_dispatch  # noqa: E402
from app.services.outbound_adapters.whatsapp_native import (  # noqa: E402
    dispatch_whatsapp_native_outbound,
    resolve_whatsapp_native_route,
)


def _uid() -> str:
    return uuid.uuid4().hex[:10]


def _settings(**overrides):
    values = {
        "whatsapp_native_enabled": True,
        "whatsapp_sidecar_url": "http://127.0.0.1:18793",
        "whatsapp_sidecar_token": "sidecar-token",
        "whatsapp_sidecar_timeout_seconds": 8,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "failed",
                request=httpx.Request("POST", "http://127.0.0.1:18793/accounts/wa-main/send"),
                response=httpx.Response(self.status_code),
            )


class FakeClient:
    def __init__(self, response=None, exc=None):
        self.response = response or FakeResponse({"ok": True, "status": "sent", "provider_message_id": "wamid.1", "sent_at": "2026-06-11T12:00:00Z"})
        self.exc = exc
        self.requests = []

    def post(self, url, *, headers, json, timeout):
        self.requests.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        if self.exc:
            raise self.exc
        return self.response


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "whatsapp_native_outbound_adapter.db"
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


def _account(db_session, *, account_id="wa-main") -> ChannelAccount:
    row = ChannelAccount(provider="whatsapp", account_id=account_id, display_name="WhatsApp Main", is_active=True, priority=10)
    db_session.add(row)
    db_session.flush()
    return row


def test_whatsapp_native_route_resolution_uses_active_account_and_ticket_target(db_session):
    ticket = _ticket(db_session, contact="+15550123456")
    _account(db_session, account_id="wa-main")
    message = _message(db_session, ticket)

    route = resolve_whatsapp_native_route(db_session, message=message, ticket=ticket)

    assert route.account_id == "wa-main"
    assert route.target == "+15550123456"
    assert route.chat_jid is None


def test_whatsapp_native_route_resolution_does_not_require_legacy_link_table(db_session, monkeypatch):
    ticket = _ticket(db_session, contact="41798559737@s.whatsapp.net")
    _account(db_session, account_id="wa-main")
    message = _message(db_session, ticket)
    db_session.execute(text("DROP TABLE external_channel_conversation_links"))
    monkeypatch.setattr(
        "app.services.outbound_adapters.whatsapp_native.get_settings",
        lambda: _settings(external_channel_sync_enabled=False),
    )

    route = resolve_whatsapp_native_route(db_session, message=message, ticket=ticket)

    assert route.account_id == "wa-main"
    assert route.target == "41798559737@s.whatsapp.net"
    assert route.chat_jid == "41798559737@s.whatsapp.net"
    assert route.source == "ticket.source_chat_id:market_or_global_whatsapp_account"


def test_whatsapp_native_send_payload_calls_sidecar(db_session, monkeypatch):
    ticket = _ticket(db_session, contact="+15550123456")
    _account(db_session, account_id="wa-main")
    message = _message(db_session, ticket, body="hello customer")
    client = FakeClient()
    monkeypatch.setattr("app.services.outbound_adapters.whatsapp_native.get_settings", lambda: _settings())

    status_value, provider_status, sent_at, route = dispatch_whatsapp_native_outbound(
        db_session,
        message=message,
        ticket=ticket,
        idempotency_key="nexusdesk-outbound-1",
        client=client,
    )

    assert status_value == MessageStatus.sent
    assert provider_status == "whatsapp_native_sent"
    assert sent_at.isoformat().startswith("2026-06-11T12:00:00")
    assert route["adapter"] == "whatsapp_native_sidecar"
    assert route["provider_message_id"] == "wamid.1"
    assert client.requests == [{
        "url": "http://127.0.0.1:18793/accounts/wa-main/send",
        "headers": {"Authorization": "Bearer sidecar-token"},
        "json": {
            "idempotency_key": "nexusdesk-outbound-1",
            "target": "+15550123456",
            "chat_jid": None,
            "body": "hello customer",
            "reply_to_message_id": None,
            "metadata": {"ticket_id": ticket.id, "outbound_message_id": message.id},
        },
        "timeout": 8.0,
    }]


def test_whatsapp_native_missing_target_is_non_retryable(db_session):
    ticket = _ticket(db_session, contact="")
    ticket.source_chat_id = None
    ticket.preferred_reply_contact = None
    ticket.customer.phone = None
    _account(db_session)
    message = _message(db_session, ticket)

    status_value, provider_status, sent_at, route = dispatch_whatsapp_native_outbound(db_session, message=message, ticket=ticket, idempotency_key="idem")

    assert status_value == MessageStatus.failed
    assert provider_status == "missing_whatsapp_target"
    assert sent_at is None
    assert route["retryable"] is False


def test_whatsapp_native_sidecar_timeout_is_retryable(db_session, monkeypatch):
    ticket = _ticket(db_session)
    _account(db_session)
    message = _message(db_session, ticket)
    monkeypatch.setattr("app.services.outbound_adapters.whatsapp_native.get_settings", lambda: _settings())
    client = FakeClient(exc=httpx.TimeoutException("timeout"))

    status_value, provider_status, _, route = dispatch_whatsapp_native_outbound(db_session, message=message, ticket=ticket, idempotency_key="idem", client=client)

    assert status_value == MessageStatus.failed
    assert provider_status == "whatsapp_native_sidecar_timeout"
    assert route["retryable"] is True


def test_native_dispatch_mode_non_retryable_sidecar_failure_marks_dead(db_session, monkeypatch):
    ticket = _ticket(db_session)
    _account(db_session)
    message = _message(db_session, ticket)
    message_dispatch_settings = message_dispatch.settings
    monkeypatch.setattr(message_dispatch_settings, "enable_outbound_dispatch", True)
    monkeypatch.setattr(message_dispatch_settings, "outbound_provider", "native")
    monkeypatch.setattr(message_dispatch_settings, "whatsapp_dispatch_mode", "native_sidecar")
    monkeypatch.setattr(message_dispatch, "log_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(message_dispatch, "_enforce_outbound_safety", lambda *args, **kwargs: True)

    def fake_native(*args, **kwargs):
        return MessageStatus.failed, "invalid_target", None, {
            "adapter": "whatsapp_native_sidecar",
            "channel": "whatsapp",
            "failure_code": "invalid_target",
            "error": "invalid target",
            "retryable": False,
        }

    monkeypatch.setattr(message_dispatch, "dispatch_whatsapp_native_outbound", fake_native)

    processed = message_dispatch.process_outbound_message(db_session, message)

    assert processed.status == MessageStatus.dead
    assert processed.failure_code == "invalid_target"
    assert processed.failure_reason == "invalid target"
