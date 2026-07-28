from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.responses import RedirectResponse
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/nexus-whatsapp-ticketless-media-privacy.db",
)
os.environ.setdefault("TENANT_RUNTIME_AUTHORITY_MODE", "enforce")

from app.api import whatsapp_media_operator
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
from app.services import whatsapp_media_service, whatsapp_privacy_lifecycle
from app.services.data_lifecycle_service import (
    build_data_subject_export,
    create_data_subject_request,
    execute_data_subject_deletion,
    qualify_data_subject_request,
)
from app.services.whatsapp_media_service import (
    get_or_create_inbound_media_asset,
    persist_inbound_media_bytes,
    project_available_inbound_media_for_ticket,
)
from app.utils.time import utc_now
from app.webchat_models import WebchatConversation, WebchatMessage

register_all_models()


class FakeStorage:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def persist_bytes(self, *, content: bytes, filename: str, media_type: str, **_kwargs):
        return SimpleNamespace(
            storage_key=f"whatsapp/{filename}",
            detected_mime_type=media_type,
            size_bytes=len(content),
        )

    def download_url(self, storage_key: str, *, filename: str, media_type: str):
        return f"https://storage.example.invalid/{storage_key}?filename={filename}&type={media_type}"

    def resolve(self, storage_key: str) -> Path:
        return Path("/tmp") / storage_key

    def delete(self, storage_key: str):
        self.deleted.append(storage_key)
        return SimpleNamespace(deleted=True, already_absent=False)


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ticketless-media-privacy.db'}",
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


def _ticketless_fixture(db_session):
    tenant = Tenant(
        tenant_key="ticketless-media",
        display_name="Ticketless Media",
        is_active=True,
    )
    db_session.add(tenant)
    db_session.flush()
    actor = SimpleNamespace(id=880, tenant_id=tenant.id, team_id=None)
    customer = Customer(
        tenant_id=tenant.id,
        name="Ticketless Customer",
        email="ticketless@example.com",
        email_normalized="ticketless@example.com",
        phone="+15550000880",
        phone_normalized="+15550000880",
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
        visitor_token_hash="f" * 64,
        tenant_key=tenant.tenant_key,
        channel_key=SourceChannel.whatsapp.value,
        visitor_phone=customer.phone,
        visitor_ref="whatsapp:15550000880@s.whatsapp.net",
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
        channel_key=SourceChannel.whatsapp.value,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    message = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=None,
        direction="visitor",
        body="<media:image>",
        body_text="<media:image>",
        message_type="imageMessage",
        delivery_status="sent",
        created_at=utc_now(),
    )
    db_session.add_all([control, message])
    db_session.flush()
    inbound = WhatsAppInboundMessage(
        channel_account_id=account.id,
        account_id=account.account_id,
        external_message_id="wa-ticketless-media-1",
        chat_jid="15550000880@s.whatsapp.net",
        sender_jid="15550000880@s.whatsapp.net",
        sender_phone=customer.phone,
        message_type="imageMessage",
        body_text="<media:image>",
        raw_payload_json={"message_type": "imageMessage"},
        received_at=utc_now(),
        processed_at=utc_now(),
        conversation_id=conversation.id,
        webchat_message_id=message.id,
        ticket_id=None,
    )
    db_session.add(inbound)
    db_session.flush()
    return actor, tenant, customer, account, connection, conversation, message, inbound


def test_ticketless_media_projects_to_conversation_download_and_later_ticket(
    db_session,
    monkeypatch,
):
    (
        _actor,
        tenant,
        customer,
        account,
        _connection,
        conversation,
        message,
        inbound,
    ) = _ticketless_fixture(db_session)
    storage = FakeStorage()
    monkeypatch.setattr(
        whatsapp_media_service,
        "get_whatsapp_media_settings",
        lambda: SimpleNamespace(enabled=True),
    )
    monkeypatch.setattr(
        whatsapp_media_service,
        "max_bytes_for_kind",
        lambda _kind: 5 * 1024 * 1024,
    )
    monkeypatch.setattr(
        whatsapp_media_service,
        "allowed_mime_types_for_kind",
        lambda _kind: {"image/png"},
    )
    monkeypatch.setattr(
        whatsapp_media_service,
        "scan_whatsapp_media",
        lambda _content: SimpleNamespace(status="clean", signature=None),
    )
    monkeypatch.setattr(whatsapp_media_service, "get_storage_backend", lambda: storage)

    asset = get_or_create_inbound_media_asset(
        db_session,
        inbound=inbound,
        provider="baileys",
        provider_media_id=inbound.external_message_id,
        media_kind="image",
        declared_mime_type="image/png",
        file_name="proof.png",
    )
    stored = persist_inbound_media_bytes(
        db_session,
        asset=asset,
        content=b"clean-image-content",
        declared_mime_type="image/png",
        file_name="proof.png",
    )

    payload = json.loads(message.payload_json or "{}")
    assert payload["media"]["asset_id"] == asset.id
    assert payload["media"]["status"] == "available"
    assert payload["media"]["download_path"] == (
        f"/api/support/conversations/{conversation.public_id}/media/{asset.id}"
    )
    assert stored.attachment_id is None
    assert asset.ticket_attachment_id is None

    monkeypatch.setattr(
        whatsapp_media_operator,
        "ensure_conversation_visible",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        whatsapp_media_operator,
        "get_storage_backend",
        lambda: storage,
    )
    response = whatsapp_media_operator.download_conversation_whatsapp_media(
        conversation_public_id=conversation.public_id,
        asset_id=asset.id,
        db=db_session,
        current_user=SimpleNamespace(id=901),
    )
    assert isinstance(response, RedirectResponse)

    ticket = Ticket(
        tenant_id=tenant.id,
        ticket_no="WA-MEDIA-LATER-1",
        title="Media case",
        description="Media case",
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

    projected = project_available_inbound_media_for_ticket(
        db_session,
        conversation_id=conversation.id,
        ticket_id=ticket.id,
    )

    assert projected == 1
    assert inbound.ticket_id == ticket.id
    assert message.ticket_id == ticket.id
    assert conversation.ticket_id == ticket.id
    assert asset.ticket_attachment_id is not None
    attachment = db_session.get(TicketAttachment, asset.ticket_attachment_id)
    assert attachment is not None
    assert attachment.ticket_id == ticket.id
    assert attachment.storage_key == stored.storage_key


def test_ticketless_only_dsar_exports_and_deletes_whatsapp_media_bytes(
    db_session,
    monkeypatch,
):
    actor, _tenant, customer, _account, connection, conversation, _message, inbound = (
        _ticketless_fixture(db_session)
    )
    storage = FakeStorage()
    asset = WhatsAppMediaAsset(
        tenant_id=customer.tenant_id,
        connection_id=connection.id,
        inbound_message_id=inbound.id,
        provider="baileys",
        provider_media_id="ticketless-private-media",
        media_kind="image",
        file_name="ticketless-private.png",
        declared_mime_type="image/png",
        detected_mime_type="image/png",
        byte_size=44,
        sha256="b" * 64,
        storage_status="available",
        scan_status="clean",
        storage_key="whatsapp/ticketless-private.png",
        downloaded_at=utc_now(),
        scanned_at=utc_now(),
        available_at=utc_now(),
    )
    db_session.add(asset)
    db_session.flush()

    export_request, _ = create_data_subject_request(
        db_session,
        actor=actor,
        customer_id=customer.id,
        request_key="ticketless-media-export",
        request_type="export",
    )
    qualify_data_subject_request(
        db_session,
        actor=actor,
        request_id=export_request.id,
        identity_evidence="ticketless@example.com",
    )
    exported = build_data_subject_export(
        db_session,
        actor=actor,
        request_id=export_request.id,
    )

    assert exported["tickets"] == []
    assert exported["whatsapp_inbound_messages"][0]["conversation_id"] == conversation.id
    assert exported["whatsapp_media_assets"][0]["id"] == asset.id

    monkeypatch.setattr(
        whatsapp_privacy_lifecycle,
        "get_storage_backend",
        lambda: storage,
    )
    delete_request, _ = create_data_subject_request(
        db_session,
        actor=actor,
        customer_id=customer.id,
        request_key="ticketless-media-delete",
        request_type="delete",
    )
    qualify_data_subject_request(
        db_session,
        actor=actor,
        request_id=delete_request.id,
        identity_evidence="ticketless@example.com",
    )
    receipt = execute_data_subject_deletion(
        db_session,
        actor=actor,
        request_id=delete_request.id,
    )

    assert receipt.ticket_count == 0
    assert receipt.conversation_count == 1
    assert "whatsapp/ticketless-private.png" in storage.deleted
    assert inbound.sender_phone is None
    assert inbound.body_text == "[redacted by privacy request]"
    assert asset.storage_key is None
    assert asset.storage_status == "deleted"
    assert asset.sha256 is None
    assert delete_request.status == "completed"
