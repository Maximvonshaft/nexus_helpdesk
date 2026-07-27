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
    "sqlite:////tmp/nexus-whatsapp-outbound-parts.db",
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
    TicketOutboundAttachment,
    TicketOutboundMessage,
)
from app.models_whatsapp import WhatsAppConnection
from app.models_whatsapp_outbound import WhatsAppOutboundPart
from app.services.whatsapp_attachment_bytes import WhatsAppAttachmentBytes
from app.services.whatsapp_baileys_sidecar import BaileysSidecarError
from app.services.whatsapp_outbound_parts import dispatch_whatsapp_parts
from app.utils.time import utc_now

register_all_models()


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'whatsapp-outbound-parts.db'}",
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


def _fixture(db_session, *, transport: str, attachment_count: int = 2):
    tenant = Tenant(
        tenant_key=f"parts-{transport}",
        display_name=f"Parts {transport}",
        is_active=True,
    )
    db_session.add(tenant)
    db_session.flush()
    account = ChannelAccount(
        tenant_id=tenant.id,
        provider="whatsapp",
        account_id=f"wa-{transport}",
        display_name=f"WhatsApp {transport}",
        is_active=True,
    )
    db_session.add(account)
    db_session.flush()
    connection = WhatsAppConnection(
        tenant_id=tenant.id,
        channel_account_id=account.id,
        transport=transport,
        sidecar_session_key=(f"wa-{transport}" if transport == "baileys_sidecar" else None),
        waba_id=("123456789" if transport == "meta_cloud_api" else None),
        phone_number_id=("987654321" if transport == "meta_cloud_api" else None),
        graph_api_version=("v23.0" if transport == "meta_cloud_api" else None),
        access_token_encrypted=("encrypted" if transport == "meta_cloud_api" else None),
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
        name="Customer",
        phone="+15550000001",
    )
    db_session.add_all([connection, customer])
    db_session.flush()
    ticket = Ticket(
        tenant_id=tenant.id,
        ticket_no=f"WA-PARTS-{transport}",
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
        body="Customer-visible text",
        delivery_status="queued",
    )
    db_session.add(message)
    db_session.flush()
    for index in range(attachment_count):
        attachment = TicketAttachment(
            ticket_id=ticket.id,
            file_name=f"attachment-{index}.pdf",
            storage_key=f"attachment-{index}.pdf",
            mime_type="application/pdf",
            file_size=4,
            visibility=NoteVisibility.external,
        )
        db_session.add(attachment)
        db_session.flush()
        db_session.add(
            TicketOutboundAttachment(
                outbound_message_id=message.id,
                attachment_id=attachment.id,
            )
        )
    db_session.flush()
    return connection, message


def test_baileys_retry_resumes_only_failed_part(db_session, monkeypatch):
    connection, message = _fixture(
        db_session,
        transport="baileys_sidecar",
        attachment_count=2,
    )
    calls: list[tuple[str, str]] = []
    failed_once = {"value": False}

    def fake_load(attachment):
        return WhatsAppAttachmentBytes(
            content=b"pdf!",
            media_kind="document",
            media_type="application/pdf",
            filename=attachment.file_name,
        )

    def fake_text(*_args, idempotency_key: str, **_kwargs):
        calls.append(("text", idempotency_key))
        return SimpleNamespace(
            provider_message_id=f"provider-{idempotency_key}",
            sent_at=utc_now(),
        )

    def fake_media(*_args, idempotency_key: str, filename: str, **_kwargs):
        calls.append((filename, idempotency_key))
        if filename == "attachment-1.pdf" and not failed_once["value"]:
            failed_once["value"] = True
            raise BaileysSidecarError(
                "temporary_media_failure",
                "temporary_media_failure",
                retryable=True,
            )
        return SimpleNamespace(
            provider_message_id=f"provider-{idempotency_key}",
            sent_at=utc_now(),
        )

    monkeypatch.setattr(
        "app.services.whatsapp_outbound_parts.load_whatsapp_attachment",
        fake_load,
    )
    monkeypatch.setattr(
        "app.services.whatsapp_outbound_parts.send_baileys_text",
        fake_text,
    )
    monkeypatch.setattr(
        "app.services.whatsapp_outbound_parts.send_baileys_media",
        fake_media,
    )

    first = dispatch_whatsapp_parts(
        db_session,
        connection=connection,
        message=message,
        target="+15550000001",
    )
    assert first.ok is False
    assert first.retryable is True
    parts = (
        db_session.query(WhatsAppOutboundPart)
        .filter(WhatsAppOutboundPart.outbound_message_id == message.id)
        .order_by(WhatsAppOutboundPart.sequence.asc())
        .all()
    )
    assert [part.status for part in parts] == ["sent", "sent", "failed"]
    first_call_count = len(calls)

    second = dispatch_whatsapp_parts(
        db_session,
        connection=connection,
        message=message,
        target="+15550000001",
    )
    assert second.ok is True
    assert len(second.provider_message_ids) == 3
    assert len(calls) == first_call_count + 1
    assert calls[-1][0] == "attachment-1.pdf"
    assert [part.status for part in parts] == ["sent", "sent", "sent"]


def test_meta_uses_same_part_authority(db_session, monkeypatch):
    connection, message = _fixture(
        db_session,
        transport="meta_cloud_api",
        attachment_count=1,
    )
    observed: list[str] = []

    monkeypatch.setattr(
        "app.services.whatsapp_outbound_parts._meta_access_token",
        lambda _connection: "meta-token",
    )
    monkeypatch.setattr(
        "app.services.whatsapp_outbound_parts.load_whatsapp_attachment",
        lambda attachment: WhatsAppAttachmentBytes(
            content=b"pdf!",
            media_kind="document",
            media_type="application/pdf",
            filename=attachment.file_name,
        ),
    )
    monkeypatch.setattr(
        "app.services.whatsapp_outbound_parts.send_meta_cloud_text",
        lambda *_args, **_kwargs: SimpleNamespace(
            provider_message_id="meta-text",
            sent_at=utc_now(),
        ),
    )

    def fake_meta_media(*_args, filename: str, **_kwargs):
        observed.append(filename)
        return SimpleNamespace(
            provider_media_id="meta-media",
            provider_message_id="meta-document",
            sent_at=utc_now(),
        )

    monkeypatch.setattr(
        "app.services.whatsapp_outbound_parts.send_meta_cloud_media",
        fake_meta_media,
    )

    result = dispatch_whatsapp_parts(
        db_session,
        connection=connection,
        message=message,
        target="+15550000001",
    )
    assert result.ok is True
    assert result.provider_message_ids == ("meta-text", "meta-document")
    assert observed == ["attachment-0.pdf"]
    parts = (
        db_session.query(WhatsAppOutboundPart)
        .filter(WhatsAppOutboundPart.outbound_message_id == message.id)
        .order_by(WhatsAppOutboundPart.sequence.asc())
        .all()
    )
    assert [part.status for part in parts] == ["sent", "sent"]
    assert parts[1].provider_media_id == "meta-media"
