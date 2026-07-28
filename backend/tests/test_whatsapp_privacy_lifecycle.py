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
from app.models_agent_routing import ConversationControl
from app.models_whatsapp import WhatsAppConnection, WhatsAppMediaAsset
from app.models_whatsapp_outbound import WhatsAppOutboundPart
from app.services import whatsapp_privacy_lifecycle
from app.services.data_lifecycle_service import (
    build_data_subject_export,
    create_data_subject_request,
    execute_data_subject_deletion,
    qualify_data_subject_request,
)
from app.utils.time import utc_now
from app.webchat_models import WebchatConversation, WebchatMessage

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


def _ticketless_fixture(db_session):
    tenant = Tenant(
        tenant_key="whatsapp-ticketless-privacy",
        display_name="WhatsApp Ticketless Privacy",
        is_active=True,
    )
    db_session.add(tenant)
    db_session.flush()
    actor = SimpleNamespace(id=701, tenant_id=tenant.id, team_id=None)
    customer = Customer(
        tenant_id=tenant.id,
        name="Ticketless Privacy Customer",
        email="ticketless-privacy@example.com",
        email_normalized="ticketless-privacy@example.com",
        phone="+15550000002",
        phone_normalized="+15550000002",
        external_ref="ticketless-privacy-customer",
    )
    account = ChannelAccount(
        tenant_id=tenant.id,
        provider="whatsapp",
        account_id="wa-ticketless-privacy",
        display_name="WhatsApp Ticketless Privacy",
        is_active=True,
    )
    db_session.add_all([customer, account])
    db_session.flush()
    connection = WhatsAppConnection(
        tenant_id=tenant.id,
        channel_account_id=account.id,
        transport="baileys_sidecar",
        sidecar_session_key="wa-ticketless-privacy",
        desired_state="active",
        observed_state="connected",
        authentication_state="linked",
        listener_state="active",
        verification_state="verified",
        desired_generation=1,
        observed_generation=1,
    )
    conversation = WebchatConversation(
        public_id="wa_ticketless_privacy",
        visitor_token_hash="b" * 64,
        tenant_key=tenant.tenant_key,
        channel_key="whatsapp",
        visitor_phone=customer.phone,
        visitor_ref="whatsapp:15550000002@s.whatsapp.net",
        origin="whatsapp-baileys_sidecar",
        status="open",
        last_seen_at=utc_now(),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db_session.add_all([connection, conversation])
    db_session.flush()
    control = ConversationControl(
        conversation_id=conversation.id,
        customer_id=customer.id,
        tenant_key=tenant.tenant_key,
        country_code="US",
        channel_key="whatsapp",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    message = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=None,
        direction="visitor",
        body="ticketless private document",
        body_text="ticketless private document",
        message_type="document",
        client_message_id="wamid.ticketless.private",
        delivery_status="sent",
        author_label="Ticketless Customer",
        created_at=utc_now(),
    )
    db_session.add_all([control, message])
    db_session.flush()
    inbound = WhatsAppInboundMessage(
        channel_account_id=account.id,
        account_id=account.account_id,
        external_message_id="wamid.ticketless.private",
        chat_jid="15550000002@s.whatsapp.net",
        sender_jid="15550000002@s.whatsapp.net",
        sender_phone="+15550000002",
        message_type="document",
        body_text="ticketless private document",
        raw_payload_json={"media_id": "ticketless-private-media"},
        received_at=utc_now(),
        processed_at=utc_now(),
        ticket_id=None,
        conversation_id=conversation.id,
        webchat_message_id=message.id,
    )
    db_session.add(inbound)
    db_session.flush()
    media = WhatsAppMediaAsset(
        tenant_id=tenant.id,
        connection_id=connection.id,
        inbound_message_id=inbound.id,
        provider="baileys",
        provider_media_id="ticketless-private-media",
        media_kind="document",
        file_name="ticketless-private.pdf",
        declared_mime_type="application/pdf",
        detected_mime_type="application/pdf",
        byte_size=100,
        sha256="b" * 64,
        storage_status="available",
        scan_status="clean",
        storage_key="ticketless-private-storage.pdf",
        downloaded_at=utc_now(),
        scanned_at=utc_now(),
        available_at=utc_now(),
    )
    db_session.add(media)
    db_session.flush()
    return actor, customer, conversation, message, inbound, media


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


def test_ticketless_whatsapp_export_and_deletion_follow_conversation_control(
    db_session,
    monkeypatch,
):
    actor, customer, conversation, message, inbound, media = _ticketless_fixture(db_session)
    export_request, _created = create_data_subject_request(
        db_session,
        actor=actor,
        customer_id=customer.id,
        request_key="whatsapp-ticketless-export",
        request_type="export",
    )
    qualify_data_subject_request(
        db_session,
        actor=actor,
        request_id=export_request.id,
        identity_evidence="ticketless-privacy@example.com",
    )

    payload = build_data_subject_export(
        db_session,
        actor=actor,
        request_id=export_request.id,
    )

    assert payload["tickets"] == []
    assert payload["whatsapp_inbound_messages"][0]["conversation_id"] == conversation.id
    assert payload["whatsapp_inbound_messages"][0]["webchat_message_id"] == message.id
    assert payload["whatsapp_media_assets"][0]["id"] == media.id

    deleted_keys: list[str] = []
    monkeypatch.setattr(
        whatsapp_privacy_lifecycle,
        "get_storage_backend",
        lambda: SimpleNamespace(
            delete=lambda key: (
                deleted_keys.append(key)
                or SimpleNamespace(deleted=True, already_absent=False)
            )
        ),
    )
    delete_request, _created = create_data_subject_request(
        db_session,
        actor=actor,
        customer_id=customer.id,
        request_key="whatsapp-ticketless-delete",
        request_type="delete",
    )
    qualify_data_subject_request(
        db_session,
        actor=actor,
        request_id=delete_request.id,
        identity_evidence="ticketless-privacy@example.com",
    )

    receipt = execute_data_subject_deletion(
        db_session,
        actor=actor,
        request_id=delete_request.id,
    )

    assert receipt.ticket_count == 0
    assert receipt.conversation_count == 1
    assert deleted_keys == ["ticketless-private-storage.pdf"]
    assert inbound.sender_phone is None
    assert inbound.body_text == "[redacted by privacy request]"
    assert inbound.raw_payload_json is None
    assert media.storage_key is None
    assert media.storage_status == "deleted"
    assert message.body == "[redacted by privacy request]"
    assert message.payload_json is None
    assert delete_request.status == "completed"
