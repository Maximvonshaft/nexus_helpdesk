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
    "sqlite:////tmp/nexus-ticketless-whatsapp-delivery.db",
)
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

from app.db import Base
from app.enums import JobStatus, SourceChannel
from app.model_registry import register_all_models
from app.models import ChannelAccount, Customer, Tenant
from app.models_agent_routing import ConversationControl
from app.models_whatsapp import WhatsAppConnection
from app.services import background_jobs, webchat_channel_delivery_service
from app.services.background_job_scope import PURPOSE_BY_JOB_TYPE
from app.services.customer_visible_message_service import create_customer_visible_message
from app.utils.time import utc_now
from app.webchat_models import WebchatConversation, WebchatMessage

register_all_models()


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ticketless-whatsapp-delivery.db'}",
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
        tenant_key="ticketless-whatsapp",
        display_name="Ticketless WhatsApp",
        is_active=True,
    )
    db_session.add(tenant)
    db_session.flush()
    customer = Customer(
        tenant_id=tenant.id,
        name="WhatsApp Customer",
        phone="+15551234567",
    )
    account = ChannelAccount(
        tenant_id=tenant.id,
        provider=SourceChannel.whatsapp.value,
        account_id="wa-ticketless",
        display_name="WhatsApp Ticketless",
        is_active=True,
    )
    db_session.add_all([customer, account])
    db_session.flush()
    connection = WhatsAppConnection(
        tenant_id=tenant.id,
        channel_account_id=account.id,
        transport="baileys_sidecar",
        sidecar_session_key="wa-ticketless",
        desired_state="active",
        observed_state="connected",
        authentication_state="linked",
        listener_state="active",
        verification_state="verified",
        desired_generation=3,
        observed_generation=3,
    )
    conversation = WebchatConversation(
        public_id="wa_ticketless_delivery",
        visitor_token_hash="a" * 64,
        tenant_key=tenant.tenant_key,
        channel_key=SourceChannel.whatsapp.value,
        visitor_phone=customer.phone,
        visitor_ref="whatsapp:15551234567@s.whatsapp.net",
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
    visitor = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=None,
        direction="visitor",
        body="Hello",
        body_text="Hello",
        message_type="text",
        client_message_id="provider-inbound-1",
        delivery_status="sent",
        metadata_json=json.dumps(
            {
                "channel_account_id": account.id,
                "account_id": account.account_id,
                "chat_jid": "15551234567@s.whatsapp.net",
                "sender_phone": customer.phone,
                "external_message_id": "provider-inbound-1",
            }
        ),
        created_at=utc_now(),
    )
    db_session.add_all([control, visitor])
    db_session.flush()
    return customer, account, connection, conversation


def test_ticketless_whatsapp_human_reply_uses_conversation_delivery_authority(
    db_session,
):
    _customer, account, _connection, conversation = _fixture(db_session)

    result = create_customer_visible_message(
        db_session,
        ticket=None,
        conversation=conversation,
        channel=SourceChannel.web_chat,
        body="We are checking this for you.",
        origin="human_agent",
        created_by=999,
        provider_status="webchat_delivered",
        delivery_status="sent",
        metadata_json={"generated_by": "operator"},
        author_label="Support Agent",
        author_user_id=999,
        create_external_comment=False,
    )

    assert result.outbound_message is None
    assert result.webchat_message is not None
    assert result.provider_status == "whatsapp_agent_reply_queued"
    assert result.webchat_message.delivery_status == "queued"
    metadata = json.loads(result.webchat_message.metadata_json or "{}")
    assert metadata["external_send"] is True
    assert metadata["reply_channel"] == SourceChannel.whatsapp.value
    assert metadata["delivery_job_type"] == background_jobs.WEBCHAT_WHATSAPP_DELIVERY_JOB
    assert metadata["processing_purpose"] == "human_support"
    job = db_session.get(background_jobs.BackgroundJob, metadata["delivery_job_id"])
    assert job is not None
    payload = json.loads(job.payload_json)
    assert payload == {
        "conversation_id": conversation.id,
        "webchat_message_id": result.webchat_message.id,
        "channel_account_id": account.id,
    }
    assert PURPOSE_BY_JOB_TYPE[job.job_type] == "human_support"


def test_ticketless_whatsapp_delivery_job_sends_once_and_scrubs_route(
    db_session,
    monkeypatch,
):
    customer, _account, _connection, conversation = _fixture(db_session)
    visible = create_customer_visible_message(
        db_session,
        ticket=None,
        conversation=conversation,
        channel=SourceChannel.whatsapp,
        body="Your request is in progress.",
        origin="human_agent",
        created_by=999,
        provider_status="whatsapp_agent_reply_queued",
        metadata_json={"generated_by": "operator"},
        author_label="Support Agent",
        author_user_id=999,
        create_external_comment=False,
    )
    message = visible.webchat_message
    assert message is not None
    metadata = json.loads(message.metadata_json or "{}")
    job = db_session.get(background_jobs.BackgroundJob, metadata["delivery_job_id"])
    assert job is not None
    job.status = JobStatus.processing
    job.locked_by = "test-worker"
    job.locked_at = utc_now()

    monkeypatch.setattr(
        webchat_channel_delivery_service,
        "get_whatsapp_runtime_settings",
        lambda: SimpleNamespace(enabled=True),
    )
    monkeypatch.setattr(
        webchat_channel_delivery_service,
        "ensure_external_dispatch_allowed",
        lambda: None,
    )
    observed_guard: list[tuple[int, str]] = []
    monkeypatch.setattr(
        webchat_channel_delivery_service,
        "ensure_data_processing_allowed",
        lambda _db, *, customer_id, purpose: observed_guard.append(
            (customer_id, purpose)
        ),
    )
    sends: list[dict[str, object]] = []

    def send(connection, *, target, body, idempotency_key, metadata):
        sends.append(
            {
                "connection_id": connection.id,
                "target": target,
                "body": body,
                "idempotency_key": idempotency_key,
                "metadata": metadata,
            }
        )
        return SimpleNamespace(
            provider_message_id="provider-outbound-1",
            sent_at=utc_now(),
        )

    monkeypatch.setattr(
        webchat_channel_delivery_service,
        "send_baileys_text",
        send,
    )

    processed = background_jobs.process_background_job(db_session, job)

    assert processed.status == JobStatus.done
    assert len(sends) == 1
    assert sends[0]["target"] == "15551234567@s.whatsapp.net"
    assert sends[0]["body"] == "Your request is in progress."
    assert sends[0]["idempotency_key"] == f"nexusdesk-webchat-message-{message.id}"
    assert observed_guard == [(customer.id, "human_support")]
    assert message.delivery_status == "sent"
    sent_metadata = json.loads(message.metadata_json or "{}")
    assert sent_metadata["provider_message_id"] == "provider-outbound-1"
    assert sent_metadata["provider_transport"] == "baileys_sidecar"
    scrubbed = json.loads(job.payload_json or "{}")
    assert scrubbed["scrubbed"] is True
    assert scrubbed["outcome"] == "sent"
    assert "channel_account_id" not in scrubbed
    assert "conversation_id" not in scrubbed


def test_ticketless_ai_delivery_keeps_automated_ai_final_purpose(db_session):
    _customer, _account, _connection, conversation = _fixture(db_session)
    message = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=None,
        direction="agent",
        body="Automated answer",
        body_text="Automated answer",
        message_type="text",
        ai_turn_id=7,
        delivery_status="queued",
        metadata_json=json.dumps(
            {
                "generated_by": "agent_runtime",
                "provider_status": "whatsapp_ai_reply_queued",
                "external_send": True,
            }
        ),
        author_label="AI Assistant",
        created_at=utc_now(),
    )
    db_session.add(message)
    db_session.flush()

    webchat_channel_delivery_service.queue_ticketless_whatsapp_delivery(
        db_session,
        conversation=conversation,
        message=message,
    )

    metadata = json.loads(message.metadata_json or "{}")
    assert metadata["processing_purpose"] == "automated_ai"
    assert metadata["delivery_job_type"] == "webchat.whatsapp_delivery"


def test_ai_service_has_no_ticketless_whatsapp_rejection_residue():
    source = Path("backend/app/services/webchat_ai_service.py").read_text(
        encoding="utf-8"
    )
    assert "ticketless_whatsapp_not_enabled" not in source
    assert "SourceChannel.whatsapp if external else SourceChannel.web_chat" in source
    assert 'delivery_status="queued" if external else "sent"' in source
