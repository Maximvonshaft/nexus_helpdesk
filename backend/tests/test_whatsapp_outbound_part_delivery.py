from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/nexus-whatsapp-outbound-part-delivery.db",
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
    Tenant,
    Ticket,
    TicketOutboundMessage,
)
from app.models_whatsapp import WhatsAppConnection
from app.models_whatsapp_outbound import WhatsAppOutboundPart
from app.services.whatsapp_delivery import apply_whatsapp_delivery
from app.utils.time import utc_now

register_all_models()


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'whatsapp-part-delivery.db'}",
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
        tenant_key=f"part-delivery-{suffix}",
        display_name=f"Part Delivery {suffix}",
        is_active=True,
    )
    db_session.add(tenant)
    db_session.flush()
    account = ChannelAccount(
        tenant_id=tenant.id,
        provider="whatsapp",
        account_id=f"wa-parts-{suffix}",
        display_name=f"WhatsApp Parts {suffix}",
        is_active=True,
    )
    db_session.add(account)
    db_session.flush()
    connection = WhatsAppConnection(
        tenant_id=tenant.id,
        channel_account_id=account.id,
        transport="baileys_sidecar",
        sidecar_session_key=f"wa-parts-{suffix}",
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
        phone="+15550000001",
    )
    db_session.add_all([connection, customer])
    db_session.flush()
    ticket = Ticket(
        tenant_id=tenant.id,
        ticket_no=f"WA-PART-{suffix}",
        title="Parts",
        description="Parts",
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
        status=MessageStatus.processing,
        body="body",
        delivery_status="queued",
    )
    db_session.add(message)
    db_session.flush()
    first = WhatsAppOutboundPart(
        tenant_id=tenant.id,
        connection_id=connection.id,
        outbound_message_id=message.id,
        sequence=0,
        part_type="text",
        idempotency_key=f"part-{suffix}-0",
        status="queued",
        provider_message_id=f"provider-{suffix}-0",
    )
    second = WhatsAppOutboundPart(
        tenant_id=tenant.id,
        connection_id=connection.id,
        outbound_message_id=message.id,
        sequence=1,
        part_type="media",
        idempotency_key=f"part-{suffix}-1",
        status="queued",
        provider_message_id=f"provider-{suffix}-1",
    )
    db_session.add_all([first, second])
    db_session.flush()
    return connection, message, first, second


def test_parent_advances_only_when_all_parts_reach_stage(db_session):
    connection, message, first, second = _fixture(db_session)

    first_sent = apply_whatsapp_delivery(
        db_session,
        connection=connection,
        provider_message_id=first.provider_message_id,
        status="sent",
        occurred_at=utc_now(),
        provider="baileys",
    )
    assert first_sent.updated is True
    assert first.status == "sent"
    assert second.status == "queued"
    assert message.status == MessageStatus.processing
    assert message.delivery_status == "queued"

    second_sent = apply_whatsapp_delivery(
        db_session,
        connection=connection,
        provider_message_id=second.provider_message_id,
        status="sent",
        occurred_at=utc_now(),
        provider="baileys",
    )
    assert second_sent.updated is True
    assert message.status == MessageStatus.sent
    assert message.delivery_status == "sent"

    apply_whatsapp_delivery(
        db_session,
        connection=connection,
        provider_message_id=first.provider_message_id,
        status="delivered",
        occurred_at=utc_now(),
        provider="baileys",
    )
    assert message.delivery_status == "sent"

    apply_whatsapp_delivery(
        db_session,
        connection=connection,
        provider_message_id=second.provider_message_id,
        status="delivered",
        occurred_at=utc_now(),
        provider="baileys",
    )
    assert message.delivery_status == "delivered"


def test_part_receipt_cannot_cross_connection_or_tenant(db_session):
    _connection_a, _message_a, first_a, _second_a = _fixture(db_session, "a")
    connection_b, _message_b, _first_b, _second_b = _fixture(db_session, "b")

    result = apply_whatsapp_delivery(
        db_session,
        connection=connection_b,
        provider_message_id=first_a.provider_message_id,
        status="read",
        occurred_at=utc_now(),
        provider="meta",
    )
    assert result.updated is False
    assert result.reason == "outbound_message_not_found"
    assert result.outbound_message_id is None
    assert result.outbound_part_id is None
    assert first_a.status == "queued"
