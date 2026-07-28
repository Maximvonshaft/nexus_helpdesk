from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/nexus-whatsapp-ticketless-media-processing.db",
)
os.environ.setdefault("TENANT_RUNTIME_AUTHORITY_MODE", "enforce")

from app.db import Base
from app.model_registry import register_all_models
from app.models import ChannelAccount, Customer, Tenant, WhatsAppInboundMessage
from app.models_agent_routing import ConversationControl
from app.models_whatsapp import WhatsAppConnection, WhatsAppMediaAsset
from app.services import whatsapp_media_worker
from app.utils.time import utc_now
from app.webchat_models import WebchatConversation

register_all_models()


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ticketless-media-processing.db'}",
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


def _ticketless_asset(db_session):
    tenant = Tenant(
        tenant_key="ticketless-processing",
        display_name="Ticketless Processing",
        is_active=True,
    )
    db_session.add(tenant)
    db_session.flush()
    customer = Customer(
        tenant_id=tenant.id,
        name="Restricted Customer",
        phone="+15551234567",
        phone_normalized="+15551234567",
    )
    account = ChannelAccount(
        tenant_id=tenant.id,
        provider="whatsapp",
        account_id="wa-ticketless-processing",
        display_name="WhatsApp Ticketless Processing",
        is_active=True,
    )
    db_session.add_all([customer, account])
    db_session.flush()
    connection = WhatsAppConnection(
        tenant_id=tenant.id,
        channel_account_id=account.id,
        transport="meta_cloud_api",
        waba_id="waba-ticketless-processing",
        phone_number_id="phone-ticketless-processing",
        graph_api_version="v23.0",
        desired_state="active",
        observed_state="connected",
        authentication_state="linked",
        listener_state="active",
        verification_state="verified",
        desired_generation=1,
        observed_generation=1,
    )
    conversation = WebchatConversation(
        public_id="wa_ticketless_processing",
        visitor_token_hash="d" * 64,
        tenant_key=tenant.tenant_key,
        channel_key="whatsapp",
        visitor_phone=customer.phone,
        visitor_ref="whatsapp:+15551234567",
        origin="whatsapp-meta_cloud",
        status="open",
        last_seen_at=utc_now(),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db_session.add_all([connection, conversation])
    db_session.flush()
    db_session.add(
        ConversationControl(
            conversation_id=conversation.id,
            customer_id=customer.id,
            tenant_key=tenant.tenant_key,
            country_code="US",
            channel_key="whatsapp",
            created_at=utc_now(),
            updated_at=utc_now(),
        )
    )
    inbound = WhatsAppInboundMessage(
        channel_account_id=account.id,
        account_id=account.account_id,
        external_message_id="wamid.ticketless.processing",
        chat_jid="15551234567@s.whatsapp.net",
        sender_jid="15551234567@s.whatsapp.net",
        sender_phone=customer.phone,
        message_type="image",
        body_text="<media:image>",
        raw_payload_json={"media_id": "media-ticketless-processing"},
        received_at=utc_now(),
        processed_at=utc_now(),
        ticket_id=None,
        conversation_id=conversation.id,
    )
    db_session.add(inbound)
    db_session.flush()
    asset = WhatsAppMediaAsset(
        tenant_id=tenant.id,
        connection_id=connection.id,
        inbound_message_id=inbound.id,
        provider="meta",
        provider_media_id="media-ticketless-processing",
        media_kind="image",
        declared_mime_type="image/jpeg",
        storage_status="downloading",
        scan_status="pending",
        locked_by="worker-1",
        locked_at=utc_now(),
    )
    db_session.add(asset)
    db_session.flush()
    return tenant, customer, conversation, asset


def test_ticketless_media_enforces_customer_processing_restriction(
    db_session,
    monkeypatch,
):
    _tenant, customer, _conversation, asset = _ticketless_asset(db_session)
    observed: list[tuple[int, str]] = []
    monkeypatch.setattr(
        whatsapp_media_worker,
        "ensure_data_processing_allowed",
        lambda _db, *, customer_id, purpose: observed.append(
            (int(customer_id), str(purpose))
        ),
    )

    whatsapp_media_worker._enforce_asset_processing_scope(db_session, asset)

    assert observed == [(customer.id, "human_support")]


def test_ticketless_media_fails_closed_on_cross_tenant_asset_scope(db_session):
    tenant, _customer, _conversation, asset = _ticketless_asset(db_session)
    other = Tenant(
        tenant_key="other-ticketless-processing",
        display_name="Other Tenant",
        is_active=True,
    )
    db_session.add(other)
    db_session.flush()
    assert other.id != tenant.id
    asset.tenant_id = other.id
    db_session.flush()

    with pytest.raises(Exception) as exc_info:
        whatsapp_media_worker._enforce_asset_processing_scope(db_session, asset)

    assert getattr(exc_info.value, "code", None) == (
        "whatsapp_media_conversation_scope_mismatch"
    )
