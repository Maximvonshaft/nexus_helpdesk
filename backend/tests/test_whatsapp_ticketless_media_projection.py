from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/nexus-whatsapp-ticketless-media.db",
)
os.environ.setdefault("TENANT_RUNTIME_AUTHORITY_MODE", "enforce")

from app.api.whatsapp_media_operator import router as media_operator_router
from app.db import Base
from app.enums import (
    ConversationState,
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
    WhatsAppInboundMessage,
)
from app.models_agent_routing import ConversationControl
from app.models_whatsapp import WhatsAppConnection, WhatsAppMediaAsset
from app.services import whatsapp_media_service
from app.services.whatsapp_media_service import (
    persist_inbound_media_bytes,
    project_available_inbound_media_for_ticket,
)
from app.utils.time import utc_now
from app.webchat_models import WebchatConversation, WebchatMessage

register_all_models()


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'whatsapp-ticketless-media.db'}",
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
        tenant_key="ticketless-media",
        display_name="Ticketless Media",
        is_active=True,
    )
    db_session.add(tenant)
    db_session.flush()
    customer = Customer(
        tenant_id=tenant.id,
        name="Media Customer",
        phone="+15551230000",
        phone_normalized="+15551230000",
    )
    account = ChannelAccount(
        tenant_id=tenant.id,
        provider="whatsapp",
        account_id="wa-ticketless-media",
        display_name="WhatsApp Ticketless Media",
        is_active=True,
    )
    db_session.add_all([customer, account])
    db_session.flush()
    connection = WhatsAppConnection(
        tenant_id=tenant.id,
        channel_account_id=account.id,
        transport="baileys_sidecar",
        sidecar_session_key="wa-ticketless-media",
        desired_state="active",
        observed_state="connected",
        authentication_state="linked",
        listener_state="active",
        verification_state="verified",
        desired_generation=1,
        observed_generation=1,
    )
    conversation = WebchatConversation(
        public_id="wa_ticketless_media",
        visitor_token_hash="c" * 64,
        tenant_key=tenant.tenant_key,
        channel_key="whatsapp",
        visitor_phone=customer.phone,
        visitor_ref="whatsapp:15551230000@s.whatsapp.net",
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
        body="<media:image> damaged parcel",
        body_text="<media:image> damaged parcel",
        message_type="text",
        client_message_id="wamid.ticketless.media",
        delivery_status="sent",
        author_label="Media Customer",
        created_at=utc_now(),
    )
    db_session.add_all([control, message])
    db_session.flush()
    inbound = WhatsAppInboundMessage(
        channel_account_id=account.id,
        account_id=account.account_id,
        external_message_id="wamid.ticketless.media",
        chat_jid="15551230000@s.whatsapp.net",
        sender_jid="15551230000@s.whatsapp.net",
        sender_phone=customer.phone,
        message_type="imageMessage",
        body_text="<media:image> damaged parcel",
        raw_payload_json={"media_id": "wamid.ticketless.media"},
        received_at=utc_now(),
        processed_at=utc_now(),
        ticket_id=None,
        conversation_id=conversation.id,
        webchat_message_id=message.id,
    )
    db_session.add(inbound)
    db_session.flush()
    asset = WhatsAppMediaAsset(
        tenant_id=tenant.id,
        connection_id=connection.id,
        inbound_message_id=inbound.id,
        provider="baileys",
        provider_media_id="wamid.ticketless.media",
        media_kind="image",
        file_name="damaged.jpg",
        declared_mime_type="image/jpeg",
        storage_status="pending",
        scan_status="pending",
    )
    db_session.add(asset)
    db_session.flush()
    return tenant, customer, account, conversation, message, inbound, asset


def test_clean_ticketless_media_is_visible_and_later_projects_to_ticket(
    db_session,
    monkeypatch,
):
    tenant, customer, account, conversation, message, inbound, asset = _fixture(db_session)
    monkeypatch.setattr(
        whatsapp_media_service,
        "get_whatsapp_media_settings",
        lambda: SimpleNamespace(enabled=True),
    )
    monkeypatch.setattr(
        whatsapp_media_service,
        "scan_whatsapp_media",
        lambda _content: SimpleNamespace(status="clean", signature=None),
    )
    monkeypatch.setattr(
        whatsapp_media_service,
        "get_storage_backend",
        lambda: SimpleNamespace(
            persist_bytes=lambda **_kwargs: SimpleNamespace(
                storage_key="whatsapp/damaged.jpg",
                detected_mime_type="image/jpeg",
                size_bytes=4,
            )
        ),
    )

    stored = persist_inbound_media_bytes(
        db_session,
        asset=asset,
        content=b"jpeg",
        declared_mime_type="image/jpeg",
        file_name="damaged.jpg",
    )

    payload = json.loads(message.payload_json or "{}")
    assert stored.attachment_id is None
    assert payload["media"]["asset_id"] == asset.id
    assert payload["media"]["status"] == "available"
    assert payload["media"]["download_path"] == (
        f"/api/support/conversations/{conversation.public_id}/media/{asset.id}"
    )
    assert message.message_type == "image"
    assert asset.ticket_attachment_id is None

    ticket = Ticket(
        tenant_id=tenant.id,
        ticket_no="WA-MEDIA-1",
        title="Damaged parcel",
        description="Customer supplied an image",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.whatsapp,
        priority=TicketPriority.medium,
        status=TicketStatus.in_progress,
        conversation_state=ConversationState.human_owned,
        channel_account_id=account.id,
        source_chat_id=inbound.chat_jid,
    )
    db_session.add(ticket)
    db_session.flush()

    projected = project_available_inbound_media_for_ticket(
        db_session,
        conversation_id=conversation.id,
        ticket_id=ticket.id,
    )
    projected_again = project_available_inbound_media_for_ticket(
        db_session,
        conversation_id=conversation.id,
        ticket_id=ticket.id,
    )

    attachment = db_session.get(TicketAttachment, asset.ticket_attachment_id)
    assert projected == 1
    assert projected_again == 0
    assert conversation.ticket_id == ticket.id
    assert inbound.ticket_id == ticket.id
    assert message.ticket_id == ticket.id
    assert attachment is not None
    assert attachment.ticket_id == ticket.id
    assert attachment.storage_key == "whatsapp/damaged.jpg"


def test_operator_media_route_is_registered_under_conversation_authority():
    paths = {getattr(route, "path", "") for route in media_operator_router.routes}
    assert "/api/support/conversations/{conversation_public_id}/media/{asset_id}" in paths
