from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/helpdesk_suite_outbound_channel_capabilities.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.api.tickets import ticket_outbound_channel_capabilities  # noqa: E402
from app.db import Base  # noqa: E402
from app.enums import ResolutionCategory, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.models import ChannelAccount, Customer, OutboundEmailAccount, Team, Tenant, Ticket, User  # noqa: E402
from app.models_whatsapp import WhatsAppConnection  # noqa: E402
from app.services.outbound_channel_registry import (  # noqa: E402
    get_outbound_channel_capability,
    list_outbound_channel_capabilities,
    require_outbound_channel_sendable,
)
from app.services.whatsapp_runtime_settings import reset_whatsapp_runtime_settings_cache  # noqa: E402
from app.settings import get_settings  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "outbound_channel_capabilities.db"
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


def _tenant(db_session) -> Tenant:
    row = Tenant(
        tenant_key=f"outbound-{_uid()}",
        display_name="Outbound Test Tenant",
        is_active=True,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _admin(db_session, tenant: Tenant) -> User:
    row = User(
        username=f"admin-{_uid()}",
        display_name="Admin",
        email=f"admin-{_uid()}@example.test",
        password_hash="x",
        role=UserRole.admin,
        tenant_id=tenant.id,
        tenant_assignment_source="fixture",
        tenant_assignment_version="nexus.test.fixture.v1",
        is_active=True,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _ticket(
    db_session,
    *,
    tenant: Tenant,
    channel=SourceChannel.whatsapp,
    contact="+15550123456",
) -> Ticket:
    team = Team(
        name=f"Ops-{_uid()}",
        team_type="support",
        tenant_id=tenant.id,
        tenant_assignment_source="fixture",
        tenant_assignment_version="nexus.test.fixture.v1",
    )
    customer = Customer(
        name="Alice",
        phone=contact,
        email="alice@example.test",
        tenant_id=tenant.id,
        tenant_assignment_source="fixture",
        tenant_assignment_version="nexus.test.fixture.v1",
    )
    db_session.add_all([team, customer])
    db_session.flush()
    ticket = Ticket(
        ticket_no=f"T-{_uid()}",
        title="Customer message",
        description="Customer message",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=channel,
        priority=TicketPriority.medium,
        status=TicketStatus.pending_assignment,
        resolution_category=ResolutionCategory.none,
        team_id=team.id,
        source_chat_id=contact,
        preferred_reply_channel=channel.value,
        preferred_reply_contact=contact,
        tenant_id=tenant.id,
        tenant_assignment_source="fixture",
        tenant_assignment_version="nexus.test.fixture.v1",
    )
    db_session.add(ticket)
    db_session.flush()
    return ticket


def _reset_settings(monkeypatch, *, dispatch=False, provider="disabled", whatsapp=False) -> None:
    monkeypatch.setenv("ENABLE_OUTBOUND_DISPATCH", "true" if dispatch else "false")
    monkeypatch.setenv("OUTBOUND_PROVIDER", provider)
    monkeypatch.setenv("WHATSAPP_ENABLED", "true" if whatsapp else "false")
    get_settings.cache_clear()
    reset_whatsapp_runtime_settings_cache()


def _whatsapp_connection(
    db_session,
    *,
    tenant: Tenant,
    ticket: Ticket,
    verified: bool,
) -> WhatsAppConnection:
    account = ChannelAccount(
        provider=SourceChannel.whatsapp.value,
        account_id=f"wa-{_uid()}",
        display_name="WhatsApp Test",
        tenant_id=tenant.id,
        tenant_assignment_source="fixture",
        tenant_assignment_version="nexus.test.fixture.v1",
        is_active=True,
        priority=10,
    )
    db_session.add(account)
    db_session.flush()
    ticket.channel_account_id = account.id
    connection = WhatsAppConnection(
        tenant_id=tenant.id,
        channel_account_id=account.id,
        transport="baileys_sidecar",
        sidecar_session_key=f"session-{_uid()}",
        desired_state="active",
        observed_state="connected",
        authentication_state="linked",
        listener_state="active",
        verification_state="verified" if verified else "pending",
        desired_generation=1,
        observed_generation=1,
    )
    db_session.add(connection)
    db_session.flush()
    return connection


def test_registry_classifies_all_declared_channels(db_session, monkeypatch):
    _reset_settings(monkeypatch)

    rows = {item.channel: item for item in list_outbound_channel_capabilities(db=db_session)}

    assert rows["internal"].status == "not_customer_sendable"
    assert rows["internal"].customer_sendable is False
    assert rows["email"].status == "configurable"
    assert rows["email"].customer_sendable is True
    assert rows["email"].supports_send is False
    assert rows["web_chat"].dispatch_type == "local"
    assert rows["whatsapp"].dispatch_type == "external"
    assert rows["telegram"].dispatch_type == "external"
    assert rows["sms"].dispatch_type == "external"


def test_internal_is_not_customer_sendable_and_email_is_not_runtime_ready(db_session, monkeypatch):
    _reset_settings(monkeypatch, dispatch=True, provider="native")
    tenant = _tenant(db_session)
    ticket = _ticket(db_session, tenant=tenant)

    with pytest.raises(HTTPException) as internal_exc:
        require_outbound_channel_sendable(db_session, ticket=ticket, channel=SourceChannel.internal)
    assert internal_exc.value.status_code == 400
    assert internal_exc.value.detail["error_code"] == "outbound_channel_not_customer_sendable"

    with pytest.raises(HTTPException) as email_exc:
        require_outbound_channel_sendable(db_session, ticket=ticket, channel=SourceChannel.email)
    assert email_exc.value.status_code == 400
    assert email_exc.value.detail["error_code"] == "outbound_channel_not_ready"
    assert "email_account_registry" in email_exc.value.detail["missing"]


def test_email_capability_uses_account_registry_and_target_validation(db_session, monkeypatch):
    _reset_settings(monkeypatch, dispatch=True, provider="native")
    tenant = _tenant(db_session)
    ticket = _ticket(db_session, tenant=tenant, channel=SourceChannel.email, contact="alice@nexusdesk-mail.com")

    missing_account = get_outbound_channel_capability(SourceChannel.email, db=db_session, ticket=ticket)
    assert missing_account.customer_sendable is True
    assert missing_account.supports_send is False
    assert "email_account_registry" in missing_account.missing
    assert "valid_email_address" not in missing_account.missing

    db_session.add(
        OutboundEmailAccount(
            display_name="Support SMTP",
            host="smtp.example.test",
            port=587,
            username="support",
            password_encrypted="encrypted-password",
            from_address="support@example.test",
            security_mode="starttls",
            is_active=True,
            priority=10,
        )
    )
    db_session.flush()

    configured = get_outbound_channel_capability(SourceChannel.email, db=db_session, ticket=ticket)
    assert configured.configured is True
    assert configured.supports_send is True
    assert configured.missing == []

    invalid_ticket = _ticket(db_session, tenant=tenant, channel=SourceChannel.email, contact="not-an-email")
    invalid_ticket.customer.email = None
    invalid = get_outbound_channel_capability(SourceChannel.email, db=db_session, ticket=invalid_ticket)
    assert "valid_email_address" in invalid.missing


def test_whatsapp_requires_runtime_verified_connection_and_target(db_session, monkeypatch):
    _reset_settings(monkeypatch, dispatch=False, provider="disabled", whatsapp=False)
    tenant = _tenant(db_session)
    ticket = _ticket(db_session, tenant=tenant)

    cap = get_outbound_channel_capability(SourceChannel.whatsapp, db=db_session, ticket=ticket)

    assert cap.status == "configurable"
    assert cap.supports_send is False
    assert "enable_outbound_dispatch" in cap.missing
    assert "outbound_provider_native" in cap.missing
    assert "whatsapp_enabled" in cap.missing
    assert "verified_whatsapp_connection" in cap.missing


def test_whatsapp_is_ready_when_runtime_and_verified_connection_are_closed(db_session, monkeypatch):
    _reset_settings(monkeypatch, dispatch=True, provider="native", whatsapp=True)
    tenant = _tenant(db_session)
    ticket = _ticket(db_session, tenant=tenant)
    _whatsapp_connection(db_session, tenant=tenant, ticket=ticket, verified=True)

    cap = get_outbound_channel_capability(SourceChannel.whatsapp, db=db_session, ticket=ticket)

    assert cap.status == "ready"
    assert cap.supports_send is True
    assert cap.missing == []
    assert require_outbound_channel_sendable(db_session, ticket=ticket, channel=SourceChannel.whatsapp).status == "ready"


def test_whatsapp_unverified_connection_remains_fail_closed(db_session, monkeypatch):
    _reset_settings(monkeypatch, dispatch=True, provider="native", whatsapp=True)
    tenant = _tenant(db_session)
    ticket = _ticket(db_session, tenant=tenant)
    connection = _whatsapp_connection(db_session, tenant=tenant, ticket=ticket, verified=False)

    cap = get_outbound_channel_capability(SourceChannel.whatsapp, db=db_session, ticket=ticket)
    assert cap.status == "configurable"
    assert "verified_whatsapp_connection" in cap.missing

    connection.verification_state = "verified"
    db_session.flush()
    ready = get_outbound_channel_capability(SourceChannel.whatsapp, db=db_session, ticket=ticket)
    assert ready.status == "ready"
    assert ready.missing == []


def test_sms_requires_e164_target_even_when_runtime_and_account_are_ready(db_session, monkeypatch):
    _reset_settings(monkeypatch, dispatch=True, provider="native")
    tenant = _tenant(db_session)
    ticket = _ticket(db_session, tenant=tenant, channel=SourceChannel.sms, contact="0791234567")
    db_session.add(ChannelAccount(provider="sms", account_id=f"sms-{_uid()}", is_active=True, priority=10))
    db_session.flush()

    cap = get_outbound_channel_capability(SourceChannel.sms, db=db_session, ticket=ticket)

    assert cap.status == "configurable"
    assert cap.supports_send is False
    assert "valid_e164_phone" in cap.missing


def test_ticket_outbound_capabilities_endpoint_is_ticket_scoped(db_session, monkeypatch):
    _reset_settings(monkeypatch, dispatch=True, provider="native", whatsapp=True)
    tenant = _tenant(db_session)
    admin = _admin(db_session, tenant)
    ticket = _ticket(db_session, tenant=tenant)
    _whatsapp_connection(db_session, tenant=tenant, ticket=ticket, verified=True)

    payload = ticket_outbound_channel_capabilities(ticket.id, db=db_session, current_user=admin)
    rows = {item["channel"]: item for item in payload["channels"]}

    assert rows["whatsapp"]["supports_send"] is True
    assert rows["whatsapp"]["external_send"] is True
    assert rows["whatsapp"]["missing"] == []
    assert rows["internal"]["customer_sendable"] is False
    assert rows["email"]["supports_send"] is False
