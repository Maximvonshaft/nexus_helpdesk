from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/nexus-whatsapp-delivery.db")

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
from app.models import ChannelAccount, Customer, Tenant, Ticket, TicketOutboundMessage
from app.models_whatsapp import WhatsAppConnection
from app.services.whatsapp_delivery import apply_whatsapp_delivery
from app.utils.time import utc_now

register_all_models()


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'whatsapp-delivery.db'}",
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


def _fixture(db_session, suffix: str = "a"):
    tenant = Tenant(
        tenant_key=f"delivery-{suffix}",
        display_name=f"Delivery {suffix}",
        is_active=True,
    )
    db_session.add(tenant)
    db_session.flush()
    account = ChannelAccount(
        tenant_id=tenant.id,
        provider="whatsapp",
        account_id=f"wa-{suffix}",
        display_name=f"WhatsApp {suffix}",
        is_active=True,
    )
    db_session.add(account)
    db_session.flush()
    connection = WhatsAppConnection(
        tenant_id=tenant.id,
        channel_account_id=account.id,
        transport="baileys_sidecar",
        sidecar_session_key=f"wa-{suffix}",
        desired_state="active",
        observed_state="connected",
        authentication_state="linked",
        listener_state="active",
        verification_state="verified",
        desired_generation=1,
        observed_generation=1,
    )
    customer = Customer(
        tenant_id=tenant.id,
        name=f"Customer {suffix}",
        phone=f"+1555000000{suffix == 'b'}",
    )
    db_session.add_all([connection, customer])
    db_session.flush()
    ticket = Ticket(
        tenant_id=tenant.id,
        ticket_no=f"WA-DELIVERY-{suffix}",
        title="Delivery",
        description="Delivery",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.whatsapp,
        priority=TicketPriority.medium,
        status=TicketStatus.pending_assignment,
        conversation_state=ConversationState.ai_active,
        channel_account_id=account.id,
    )
    db_session.add(ticket)
    db_session.flush()
    message = TicketOutboundMessage(
        ticket_id=ticket.id,
        channel=SourceChannel.whatsapp,
        status=MessageStatus.sent,
        body="hello",
        provider_message_id=f"provider-{suffix}",
        delivery_status="sent",
        sent_at=utc_now(),
    )
    db_session.add(message)
    db_session.flush()
    return connection, message


def test_delivery_state_is_monotonic(db_session):
    connection, message = _fixture(db_session)

    delivered = apply_whatsapp_delivery(
        db_session,
        connection=connection,
        provider_message_id=message.provider_message_id,
        status="delivered",
        occurred_at=utc_now(),
        provider="meta",
        payload={"status": "delivered"},
    )
    assert delivered.updated is True
    assert message.delivery_status == "delivered"

    stale_sent = apply_whatsapp_delivery(
        db_session,
        connection=connection,
        provider_message_id=message.provider_message_id,
        status="sent",
        occurred_at=utc_now(),
        provider="meta",
    )
    assert stale_sent.updated is False
    assert stale_sent.reason == "stale_delivery_event"
    assert message.delivery_status == "delivered"

    read = apply_whatsapp_delivery(
        db_session,
        connection=connection,
        provider_message_id=message.provider_message_id,
        status="read",
        occurred_at=utc_now(),
        provider="meta",
    )
    assert read.updated is True
    assert message.delivery_status == "read"

    late_failure = apply_whatsapp_delivery(
        db_session,
        connection=connection,
        provider_message_id=message.provider_message_id,
        status="failed",
        occurred_at=utc_now(),
        provider="meta",
        error_code="late_failure",
    )
    assert late_failure.updated is False
    assert late_failure.reason == "stale_failure_after_delivery"
    assert message.status == MessageStatus.sent
    assert message.delivery_status == "read"


def test_delivery_receipt_cannot_cross_account_or_tenant(db_session):
    connection_a, message_a = _fixture(db_session, "a")
    connection_b, _message_b = _fixture(db_session, "b")

    result = apply_whatsapp_delivery(
        db_session,
        connection=connection_b,
        provider_message_id=message_a.provider_message_id,
        status="read",
        occurred_at=utc_now(),
        provider="baileys",
    )
    assert result.updated is False
    assert result.reason == "delivery_scope_mismatch"
    assert message_a.delivery_status == "sent"


def test_provider_message_identity_cannot_be_rebound(db_session):
    connection, message = _fixture(db_session)
    result = apply_whatsapp_delivery(
        db_session,
        connection=connection,
        provider_message_id="different-provider-message",
        outbound_message_id=message.id,
        status="delivered",
        occurred_at=utc_now(),
        provider="baileys",
    )
    assert result.updated is False
    assert result.reason == "provider_message_id_mismatch"
    assert message.provider_message_id == "provider-a"
