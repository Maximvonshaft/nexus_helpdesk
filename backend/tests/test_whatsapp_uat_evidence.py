from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/nexus-whatsapp-uat-evidence.db",
)

from app.db import Base
from app.enums import (
    ConversationState,
    MessageStatus,
    NoteVisibility,
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
    TicketAttachment,
    TicketOutboundMessage,
    WhatsAppInboundMessage,
)
from app.models_whatsapp import WhatsAppConnection, WhatsAppMediaAsset
from app.models_whatsapp_outbound import WhatsAppOutboundPart
from app.services.whatsapp_uat_evidence import (
    WhatsAppUatEvidenceError,
    WhatsAppUatSelection,
    collect_whatsapp_uat_facts,
)
from app.utils.time import utc_now

register_all_models()


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'whatsapp-uat-evidence.db'}",
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


def _seed(db_session):
    now = utc_now()
    tenant = Tenant(
        tenant_key="whatsapp-uat",
        display_name="WhatsApp UAT",
        is_active=True,
    )
    db_session.add(tenant)
    db_session.flush()
    account = ChannelAccount(
        tenant_id=tenant.id,
        provider="whatsapp",
        account_id="wa-uat-meta",
        display_name="WhatsApp UAT Meta",
        is_active=True,
    )
    db_session.add(account)
    db_session.flush()
    connection = WhatsAppConnection(
        tenant_id=tenant.id,
        channel_account_id=account.id,
        transport="meta_cloud_api",
        waba_id="123456789",
        phone_number_id="987654321",
        graph_api_version="v23.0",
        phone_number="+15550001234",
        desired_state="active",
        observed_state="connected",
        authentication_state="linked",
        listener_state="active",
        verification_state="verified",
        desired_generation=3,
        observed_generation=3,
        session_generation=2,
    )
    customer = Customer(
        tenant_id=tenant.id,
        name="UAT Customer",
        phone="+15550005678",
    )
    db_session.add_all([connection, customer])
    db_session.flush()
    ticket = Ticket(
        tenant_id=tenant.id,
        ticket_no="WA-UAT-0001",
        title="WhatsApp UAT",
        description="WhatsApp UAT",
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
    inbound = WhatsAppInboundMessage(
        channel_account_id=account.id,
        account_id=account.account_id,
        external_message_id="provider-inbound",
        chat_jid="15550005678@s.whatsapp.net",
        sender_jid="15550005678@s.whatsapp.net",
        sender_phone="+15550005678",
        message_type="text",
        body_text="UAT inbound",
        raw_payload_json={},
        ticket_id=ticket.id,
        received_at=now,
        processed_at=now,
        created_at=now,
    )
    media_inbound = WhatsAppInboundMessage(
        channel_account_id=account.id,
        account_id=account.account_id,
        external_message_id="provider-media-inbound",
        chat_jid="15550005678@s.whatsapp.net",
        sender_jid="15550005678@s.whatsapp.net",
        sender_phone="+15550005678",
        message_type="image",
        body_text="<media:image>",
        raw_payload_json={},
        ticket_id=ticket.id,
        received_at=now,
        processed_at=now,
        created_at=now,
    )
    db_session.add_all([inbound, media_inbound])
    db_session.flush()
    outbound = TicketOutboundMessage(
        ticket_id=ticket.id,
        channel=SourceChannel.whatsapp,
        status=MessageStatus.sent,
        body="UAT outbound",
        delivery_status="read",
        sent_at=now,
    )
    media_outbound = TicketOutboundMessage(
        ticket_id=ticket.id,
        channel=SourceChannel.whatsapp,
        status=MessageStatus.sent,
        body="",
        delivery_status="read",
        sent_at=now,
    )
    attachment = TicketAttachment(
        ticket_id=ticket.id,
        uploaded_by=None,
        file_name="uat.png",
        storage_key="uat.png",
        file_path=None,
        file_url=None,
        mime_type="image/png",
        file_size=128,
        visibility=NoteVisibility.external,
        created_at=now,
    )
    db_session.add_all([outbound, media_outbound, attachment])
    db_session.flush()
    text_part = WhatsAppOutboundPart(
        tenant_id=tenant.id,
        connection_id=connection.id,
        outbound_message_id=outbound.id,
        sequence=0,
        part_type="text",
        idempotency_key="uat-text-part",
        status="read",
        provider_message_id="provider-outbound",
        sent_at=now,
        delivered_at=now,
        read_at=now,
        receipt_at=now,
    )
    media_part = WhatsAppOutboundPart(
        tenant_id=tenant.id,
        connection_id=connection.id,
        outbound_message_id=media_outbound.id,
        attachment_id=attachment.id,
        sequence=0,
        part_type="media",
        media_kind="image",
        media_type="image/png",
        file_name="uat.png",
        idempotency_key="uat-media-part",
        status="read",
        provider_message_id="provider-media-outbound",
        sent_at=now,
        delivered_at=now,
        read_at=now,
        receipt_at=now,
    )
    asset = WhatsAppMediaAsset(
        tenant_id=tenant.id,
        connection_id=connection.id,
        inbound_message_id=media_inbound.id,
        provider="meta",
        provider_media_id="meta-media-id",
        media_kind="image",
        file_name="uat.png",
        declared_mime_type="image/png",
        detected_mime_type="image/png",
        byte_size=128,
        sha256="a" * 64,
        storage_status="available",
        scan_status="clean",
        storage_key="uat.png",
        ticket_attachment_id=attachment.id,
        downloaded_at=now,
        scanned_at=now,
        available_at=now,
    )
    db_session.add_all([text_part, media_part, asset])
    db_session.flush()
    return connection


def test_uat_evidence_is_redacted_and_bound_to_canonical_parts(db_session):
    connection = _seed(db_session)
    payload = collect_whatsapp_uat_facts(
        db_session,
        connection=connection,
        selection=WhatsAppUatSelection(
            inbound_provider_message_id="provider-inbound",
            outbound_provider_message_id="provider-outbound",
            media_inbound_provider_message_id="provider-media-inbound",
            media_outbound_provider_message_id="provider-media-outbound",
        ),
    )
    assert payload["phone_suffix"] == "1234"
    assert payload["outbound"]["status"] == "read"
    assert payload["media"]["inbound"]["scan_status"] == "clean"
    assert payload["media"]["inbound"]["storage_status"] == "available"
    assert payload["media"]["outbound"]["part_type"] == "media"
    serialized = str(payload)
    assert "+15550001234" not in serialized
    assert "+15550005678" not in serialized
    assert payload["contains_secrets"] is False
    assert payload["contains_full_phone_numbers"] is False


def test_uat_evidence_rejects_unknown_provider_message(db_session):
    connection = _seed(db_session)
    with pytest.raises(WhatsAppUatEvidenceError, match="outbound_not_found"):
        collect_whatsapp_uat_facts(
            db_session,
            connection=connection,
            selection=WhatsAppUatSelection(
                inbound_provider_message_id="provider-inbound",
                outbound_provider_message_id="unknown-outbound",
            ),
        )
