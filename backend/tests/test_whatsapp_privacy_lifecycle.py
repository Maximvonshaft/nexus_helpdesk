from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/nexus-whatsapp-privacy-lifecycle.db",
)
os.environ.setdefault("TENANT_RUNTIME_AUTHORITY_MODE", "enforce")

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
    WhatsAppInboundMessage,
)
from app.models_whatsapp import WhatsAppConnection, WhatsAppMediaAsset
from app.models_whatsapp_outbound import WhatsAppOutboundPart
from app.services.data_lifecycle_service import (
    build_data_subject_export,
    create_data_subject_request,
    execute_data_subject_deletion,
    qualify_data_subject_request,
)
from app.utils.time import utc_now

register_all_models()


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'whatsapp-privacy.db'}",
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


def _fixture(db_session):
    tenant = Tenant(
        tenant_key="whatsapp-privacy",
        display_name="WhatsApp Privacy",
        is_active=True,
    )
    db_session.add(tenant)
    db_session.flush()
    actor = SimpleNamespace(id=700, tenant_id=tenant.id, team_id=None)
    customer = Customer(
        tenant_id=tenant.id,
        name="Privacy Customer",
        email="privacy@example.com",
        email_normalized="privacy@example.com",
        phone="+15550000001",
        phone_normalized="+15550000001",
        external_ref="privacy-customer",
    )
    account = ChannelAccount(
        tenant_id=tenant.id,
        provider="whatsapp",
        account_id="wa-privacy",
        display_name="WhatsApp Privacy",
        is_active=True,
    )
    db_session.add_all([customer, account])
    db_session.flush()
    connection = WhatsAppConnection(
        tenant_id=tenant.id,
        channel_account_id=account.id,
        transport="baileys_sidecar",
        sidecar_session_key="wa-privacy",
        desired_state="active",
        observed_state="connected",
        authentication_state="linked",
        listener_state="active",
        verification_state="verified",
        desired_generation=1,
        observed_generation=1,
    )
    ticket = Ticket(
        tenant_id=tenant.id,
        ticket_no="WA-PRIVACY-1",
        title="Private issue",
        description="Private description",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.whatsapp,
        priority=TicketPriority.medium,
        status=TicketStatus.closed,
        conversation_state=ConversationState.ai_active,
        channel_account_id=account.id,
        source_chat_id="15550000001@s.whatsapp.net",
        preferred_reply_channel="whatsapp",
        preferred_reply_contact="+15550000001",
        closed_at=utc_now(),
    )
    db_session.add_all([connection, ticket])
    db_session.flush()
    inbound = WhatsAppInboundMessage(
        channel_account_id=account.id,
        account_id=account.account_id,
        external_message_id="wamid.private.inbound",
        chat_jid="15550000001@s.whatsapp.net",
        sender_jid="15550000001@s.whatsapp.net",
        sender_phone="+15550000001",
        message_type="document",
        body_text="private document",
        raw_payload_json={"media_id": "provider-private-media"},
        received_at=utc_now(),
        processed_at=utc_now(),
        ticket_id=ticket.id,
    )
    outbound = TicketOutboundMessage(
        ticket_id=ticket.id,
        channel=SourceChannel.whatsapp,
        status=MessageStatus.sent,
        body="private reply",
        provider_message_id="wamid.private.parent",
        delivery_status="sent",
        sent_at=utc_now(),
    )
    db_session.add_all([inbound, outbound])
    db_session.flush()
    media = WhatsAppMediaAsset(
        tenant_id=tenant.id,
        connection_id=connection.id,
        inbound_message_id=inbound.id,
        provider="baileys",
        provider_media_id="provider-private-media",
        media_kind="document",
        file_name="private-passport.pdf",
        declared_mime_type="application/pdf",
        detected_mime_type="application/pdf",
        byte_size=120,
        sha256="a" * 64,
        storage_status="available",
        scan_status="clean",
        storage_key="private-storage-key.pdf",
        last_error_message="private detail",
        downloaded_at=utc_now(),
        scanned_at=utc_now(),
        available_at=utc_now(),
    )
    part = WhatsAppOutboundPart(
        tenant_id=tenant.id,
        connection_id=connection.id,
        outbound_message_id=outbound.id,
        sequence=0,
        part_type="media",
        idempotency_key="privacy-part-0",
        media_kind="document",
        media_type="application/pdf",
        file_name="private-contract.pdf",
        status="sent",
        provider_media_id="provider-private-upload",
        provider_message_id="wamid.private.part",
        failure_reason="private failure detail",
        sent_at=utc_now(),
    )
    db_session.add_all([media, part])
    db_session.flush()
    return actor, customer, ticket, inbound, media, part


def test_canonical_export_contains_bounded_whatsapp_provider_records(db_session):
    actor, customer, _ticket, inbound, media, part = _fixture(db_session)
    request, _created = create_data_subject_request(
        db_session,
        actor=actor,
        customer_id=customer.id,
        request_key="whatsapp-export-1",
        request_type="export",
    )
    qualify_data_subject_request(
        db_session,
        actor=actor,
        request_id=request.id,
        identity_evidence="privacy@example.com",
    )

    payload = build_data_subject_export(
        db_session,
        actor=actor,
        request_id=request.id,
    )

    assert payload["whatsapp_inbound_messages"][0]["id"] == inbound.id
    assert payload["whatsapp_media_assets"][0]["id"] == media.id
    assert payload["whatsapp_media_assets"][0]["provider_media_id"] == "provider-private-media"
    assert payload["whatsapp_outbound_parts"][0]["id"] == part.id
    assert payload["whatsapp_outbound_parts"][0]["provider_message_id"] == "wamid.private.part"
    assert "storage_key" not in payload["whatsapp_media_assets"][0]
    assert request.result_manifest_json["counts"]["whatsapp_media_assets"] == 1


def test_canonical_deletion_redacts_whatsapp_media_and_parts(db_session):
    actor, customer, _ticket, inbound, media, part = _fixture(db_session)
    request, _created = create_data_subject_request(
        db_session,
        actor=actor,
        customer_id=customer.id,
        request_key="whatsapp-delete-1",
        request_type="delete",
    )
    qualify_data_subject_request(
        db_session,
        actor=actor,
        request_id=request.id,
        identity_evidence="privacy@example.com",
    )

    receipt = execute_data_subject_deletion(
        db_session,
        actor=actor,
        request_id=request.id,
    )

    assert receipt.related_row_count >= 3
    assert inbound.sender_phone is None
    assert inbound.body_text == "[redacted by privacy request]"
    assert media.provider_media_id.startswith("erased-whatsapp-media-")
    assert media.file_name == "[redacted by privacy request]"
    assert media.storage_key is None
    assert media.sha256 is None
    assert media.storage_status == "deleted"
    assert media.last_error_message is None
    assert part.provider_media_id is None
    assert part.provider_message_id is None
    assert part.file_name == "[redacted by privacy request]"
    assert part.failure_reason is None
    assert request.status == "completed"
    assert request.result_manifest_json["raw_values_persisted"] is False
