from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/nexus-whatsapp-ticket-account-affinity.db",
)

from app.db import Base
from app.enums import (
    ConversationState,
    MessageStatus,
    SourceChannel,
    TicketPriority,
    TicketSource,
    TicketStatus,
)
from app.model_registry import register_all_models
from app.models import (
    ChannelAccount,
    Customer,
    Market,
    Tenant,
    Ticket,
    TicketOutboundMessage,
)
from app.models_whatsapp import WhatsAppConnection
from app.services.outbound_adapters.whatsapp import dispatch_whatsapp_outbound
from app.services.whatsapp_runtime_settings import reset_whatsapp_runtime_settings_cache

register_all_models()


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ticket-account-affinity.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    Base.metadata.create_all(engine)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _connection(
    db_session,
    *,
    tenant_id: int,
    account: ChannelAccount,
    ready: bool,
) -> WhatsAppConnection:
    row = WhatsAppConnection(
        tenant_id=tenant_id,
        channel_account_id=account.id,
        transport="baileys_sidecar",
        sidecar_session_key=account.account_id,
        desired_state="active",
        observed_state="connected" if ready else "degraded",
        authentication_state="linked",
        listener_state="active" if ready else "reconnecting",
        verification_state="verified",
        desired_generation=1,
        observed_generation=1,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_ticket_bound_outbound_never_falls_through_to_another_ready_account(
    db_session,
    monkeypatch,
):
    monkeypatch.setenv("WHATSAPP_ENABLED", "true")
    reset_whatsapp_runtime_settings_cache()

    tenant = Tenant(
        tenant_key="ticket-account-affinity",
        display_name="Ticket Account Affinity",
        is_active=True,
    )
    market = Market(
        tenant_id=None,
        code="AFFINITY-CH",
        name="Affinity Switzerland",
        country_code="CH",
        is_active=True,
    )
    db_session.add_all([tenant, market])
    db_session.flush()
    market.tenant_id = tenant.id

    assigned_account = ChannelAccount(
        tenant_id=tenant.id,
        provider="whatsapp",
        account_id="wa-assigned",
        display_name="Assigned WhatsApp",
        market_id=market.id,
        is_active=True,
        priority=10,
    )
    fallback_account = ChannelAccount(
        tenant_id=tenant.id,
        provider="whatsapp",
        account_id="wa-fallback",
        display_name="Fallback WhatsApp",
        market_id=market.id,
        is_active=True,
        priority=20,
    )
    customer = Customer(
        tenant_id=tenant.id,
        name="Affinity Customer",
        phone="+41790000001",
    )
    db_session.add_all([assigned_account, fallback_account, customer])
    db_session.flush()
    _connection(
        db_session,
        tenant_id=tenant.id,
        account=assigned_account,
        ready=False,
    )
    _connection(
        db_session,
        tenant_id=tenant.id,
        account=fallback_account,
        ready=True,
    )

    ticket = Ticket(
        tenant_id=tenant.id,
        ticket_no="WA-AFFINITY-1",
        title="Assigned account outage",
        description="Assigned account outage",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.whatsapp,
        priority=TicketPriority.medium,
        status=TicketStatus.pending_assignment,
        conversation_state=ConversationState.ai_active,
        market_id=market.id,
        channel_account_id=assigned_account.id,
        source_chat_id=customer.phone,
    )
    db_session.add(ticket)
    db_session.flush()
    message = TicketOutboundMessage(
        ticket_id=ticket.id,
        channel=SourceChannel.whatsapp,
        status=MessageStatus.processing,
        body="Please retry on the assigned account.",
        delivery_status="queued",
    )
    db_session.add(message)
    db_session.flush()

    status, provider_status, sent_at, context = dispatch_whatsapp_outbound(
        db_session,
        message=message,
        ticket=ticket,
        idempotency_key="ticket-account-affinity-1",
    )

    assert status == MessageStatus.failed
    assert provider_status == "verified_whatsapp_connection_missing"
    assert sent_at is None
    assert context["failure_code"] == "verified_whatsapp_connection_missing"
    assert context["retryable"] is True
    assert "connection_id" not in context
