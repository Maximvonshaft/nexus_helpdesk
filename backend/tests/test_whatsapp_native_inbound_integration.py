from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/helpdesk_suite_whatsapp_native_inbound.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.db import Base  # noqa: E402
from app.api.whatsapp_native_integration import apply_whatsapp_native_delivery_payload  # noqa: E402
from app.enums import MessageStatus, SourceChannel, TicketPriority, TicketSource, TicketStatus  # noqa: E402
from app.models import BackgroundJob, ChannelAccount, Ticket, TicketOutboundMessage, WhatsAppInboundMessage  # noqa: E402
from app.settings import get_settings  # noqa: E402
from app.services.whatsapp_native_inbound import (  # noqa: E402
    WhatsAppNativeAuthError,
    WhatsAppNativeInboundError,
    ingest_whatsapp_native_inbound,
    verify_whatsapp_connector_headers,
)
from app.utils.time import utc_now  # noqa: E402
from app.webchat_models import WebchatAITurn, WebchatConversation, WebchatMessage  # noqa: E402

TEST_CHAT_HANDLE = "wa-test-contact"


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "whatsapp_native_inbound.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False}, future=True)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture(autouse=True)
def connector_env(monkeypatch):
    monkeypatch.setenv("WHATSAPP_CONNECTOR_KEY", "connector-key")
    monkeypatch.setenv("WHATSAPP_CONNECTOR_HMAC_SECRET", "connector-hmac-secret")
    monkeypatch.setenv("WHATSAPP_CONNECTOR_TIMESTAMP_TOLERANCE_SECONDS", "300")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _uid() -> str:
    return uuid.uuid4().hex[:10]


def _account(session, *, account_id: str = "wa-main") -> ChannelAccount:
    row = ChannelAccount(provider="whatsapp", account_id=account_id, display_name="WhatsApp Main", is_active=True, priority=10)
    session.add(row)
    session.flush()
    return row


def _payload(**overrides):
    data = {
        "account_id": "wa-main",
        "external_message_id": f"wamid.{_uid()}",
        "chat_jid": TEST_CHAT_HANDLE,
        "sender_jid": TEST_CHAT_HANDLE,
        "sender_phone": None,
        "message_type": "conversation",
        "body_text": "Hello, where is my package?",
        "raw_payload": {"key": {"id": "msg-1"}},
        "received_at": "2026-06-12T09:00:00Z",
    }
    data.update(overrides)
    return data


def _signature(secret: str, timestamp: str, raw_body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), timestamp.encode("utf-8") + b"." + raw_body, hashlib.sha256).hexdigest()


def test_connector_hmac_headers_are_verified():
    raw_body = json.dumps(_payload(), separators=(",", ":")).encode("utf-8")
    timestamp = utc_now().isoformat()
    signature = _signature("connector-hmac-secret", timestamp, raw_body)

    verify_whatsapp_connector_headers(
        raw_body=raw_body,
        connector_key="connector-key",
        account_id="wa-main",
        timestamp=timestamp,
        signature=signature,
    )

    with pytest.raises(WhatsAppNativeAuthError, match="invalid_signature"):
        verify_whatsapp_connector_headers(
            raw_body=raw_body,
            connector_key="connector-key",
            account_id="wa-main",
            timestamp=timestamp,
            signature="bad",
        )


def test_inbound_creates_ticket_webchat_projection_and_ai_turn(db_session):
    account = _account(db_session)

    result = ingest_whatsapp_native_inbound(db_session, _payload(account_id=account.account_id, external_message_id="wamid.first"))
    db_session.commit()

    assert result.ok is True
    assert result.idempotent is False
    inbound = db_session.query(WhatsAppInboundMessage).one()
    ticket = db_session.query(Ticket).one()
    conversation = db_session.query(WebchatConversation).one()
    message = db_session.query(WebchatMessage).filter(WebchatMessage.direction == "visitor").one()
    turn = db_session.query(WebchatAITurn).one()
    job = db_session.query(BackgroundJob).one()

    assert inbound.ticket_id == ticket.id
    assert inbound.conversation_id == conversation.id
    assert inbound.webchat_message_id == message.id
    assert ticket.source_channel == SourceChannel.whatsapp
    assert ticket.channel_account_id == account.id
    assert ticket.preferred_reply_channel == SourceChannel.whatsapp.value
    assert conversation.channel_key == "whatsapp"
    assert conversation.origin == "whatsapp-native"
    assert message.client_message_id == "wamid.first"
    assert turn.trigger_message_id == message.id
    assert job.queue_name == "webchat_ai_reply"
    assert result.ai_turn_id == turn.id
    assert result.ai_status == "queued"


@pytest.mark.parametrize("chat_jid", ["status@broadcast", "12345@broadcast", "12345@g.us", "12345@newsletter"])
def test_non_customer_whatsapp_chats_are_not_projected(db_session, chat_jid):
    account = _account(db_session)

    with pytest.raises(WhatsAppNativeInboundError, match="ignored_whatsapp_non_customer_chat"):
        ingest_whatsapp_native_inbound(
            db_session,
            _payload(account_id=account.account_id, external_message_id=f"wamid.{chat_jid}", chat_jid=chat_jid, sender_jid=chat_jid),
        )

    assert db_session.query(WhatsAppInboundMessage).count() == 0
    assert db_session.query(Ticket).count() == 0
    assert db_session.query(WebchatConversation).count() == 0
    assert db_session.query(WebchatMessage).count() == 0
    assert db_session.query(WebchatAITurn).count() == 0


def test_from_me_store_only_persists_raw_without_projection_or_ai(db_session):
    account = _account(db_session)

    result = ingest_whatsapp_native_inbound(
        db_session,
        _payload(
            account_id=account.account_id,
            external_message_id="wamid.self.store",
            from_me=True,
            projection_mode="store_only",
            body_text="operator self echo",
        ),
    )
    db_session.commit()

    inbound = db_session.query(WhatsAppInboundMessage).one()
    assert result.ok is True
    assert result.ticket_id is None
    assert result.conversation_id is None
    assert result.webchat_message_id is None
    assert result.ai_turn_id is None
    assert inbound.processed_at is not None
    assert inbound.ticket_id is None
    assert inbound.conversation_id is None
    assert inbound.webchat_message_id is None
    assert inbound.raw_payload_json["from_me"] is True
    assert inbound.raw_payload_json["projection_mode"] == "store_only"
    assert db_session.query(Ticket).count() == 0
    assert db_session.query(WebchatConversation).count() == 0
    assert db_session.query(WebchatMessage).count() == 0
    assert db_session.query(WebchatAITurn).count() == 0


def test_from_me_test_visitor_with_prefix_projects_and_marks_metadata(db_session):
    account = _account(db_session)

    result = ingest_whatsapp_native_inbound(
        db_session,
        _payload(
            account_id=account.account_id,
            external_message_id="wamid.self.visitor",
            from_me=True,
            projection_mode="test_visitor",
            self_echo_test_prefix="SELF_TEST",
            body_text="SELF_TEST hello from self smoke",
        ),
    )
    db_session.commit()

    inbound = db_session.query(WhatsAppInboundMessage).one()
    message = db_session.query(WebchatMessage).filter(WebchatMessage.direction == "visitor").one()
    metadata = json.loads(message.metadata_json)
    assert result.ticket_id is not None
    assert result.conversation_id is not None
    assert result.webchat_message_id == message.id
    assert inbound.ticket_id is not None
    assert inbound.conversation_id is not None
    assert inbound.webchat_message_id == message.id
    assert inbound.body_text == "hello from self smoke"
    assert message.body_text == "hello from self smoke"
    assert metadata["source"] == "self_echo_test"
    assert metadata["from_me"] is True
    assert metadata["projection_mode"] == "test_visitor"
    assert db_session.query(WebchatAITurn).count() == 1


def test_from_me_test_visitor_without_prefix_store_only(db_session):
    _account(db_session)

    result = ingest_whatsapp_native_inbound(
        db_session,
        _payload(
            external_message_id="wamid.self.no-prefix",
            from_me=True,
            projection_mode="test_visitor",
            self_echo_test_prefix="SELF_TEST",
            body_text="hello without prefix",
        ),
    )
    db_session.commit()

    inbound = db_session.query(WhatsAppInboundMessage).one()
    assert result.ticket_id is None
    assert inbound.processed_at is not None
    assert inbound.ticket_id is None
    assert db_session.query(Ticket).count() == 0
    assert db_session.query(WebchatConversation).count() == 0
    assert db_session.query(WebchatMessage).count() == 0


def test_from_me_self_chat_is_store_only_without_projection_or_ai(db_session):
    account = _account(db_session)

    result = ingest_whatsapp_native_inbound(
        db_session,
        _payload(
            account_id=account.account_id,
            external_message_id="wamid.self.chat",
            from_me=True,
            projection_mode="self_chat",
            body_text="can you please check my parcel CH020000129135",
        ),
    )
    db_session.commit()

    inbound = db_session.query(WhatsAppInboundMessage).one()
    assert result.ok is True
    assert result.ticket_id is None
    assert result.conversation_id is None
    assert result.webchat_message_id is None
    assert result.ai_turn_id is None
    assert inbound.processed_at is not None
    assert inbound.ticket_id is None
    assert inbound.conversation_id is None
    assert inbound.webchat_message_id is None
    assert db_session.query(Ticket).count() == 0
    assert db_session.query(WebchatConversation).count() == 0
    assert db_session.query(WebchatMessage).count() == 0
    assert db_session.query(WebchatAITurn).count() == 0


def test_duplicate_inbound_is_idempotent_and_does_not_duplicate_projection(db_session):
    _account(db_session)
    payload = _payload(external_message_id="wamid.duplicate")

    first = ingest_whatsapp_native_inbound(db_session, payload)
    second = ingest_whatsapp_native_inbound(db_session, payload)
    db_session.commit()

    assert first.idempotent is False
    assert second.idempotent is True
    assert db_session.query(WhatsAppInboundMessage).count() == 1
    assert db_session.query(WebchatMessage).filter(WebchatMessage.direction == "visitor").count() == 1
    assert db_session.query(WebchatAITurn).count() == 1


def test_native_delivery_payload_records_provider_receipt(db_session):
    ticket = Ticket(
        ticket_no="WA-1",
        title="WhatsApp delivery",
        description="Outbound receipt test",
        source=TicketSource.user_message,
        source_channel=SourceChannel.whatsapp,
        priority=TicketPriority.medium,
        status=TicketStatus.new,
    )
    db_session.add(ticket)
    db_session.flush()
    outbound = TicketOutboundMessage(
        ticket_id=ticket.id,
        channel=SourceChannel.whatsapp,
        status=MessageStatus.pending,
        body="Hallo",
        provider_status="whatsapp_ai_reply_queued",
        provider_message_id="nexusdesk-outbound-1",
    )
    db_session.add(outbound)
    db_session.flush()

    apply_whatsapp_native_delivery_payload(
        outbound,
        {
            "status": "sent",
            "provider_message_id": "BAE5REMOTE123",
            "sent_at": "2026-07-06T18:40:42.531Z",
            "idempotency_key": "nexusdesk-outbound-1",
        },
    )

    assert outbound.status == MessageStatus.sent
    assert outbound.provider_status == "whatsapp_native_sent"
    assert outbound.provider_message_id == "BAE5REMOTE123"
    assert outbound.delivery_status == "sent"
    assert outbound.delivery_receipt_provider == "whatsapp_native"
    assert outbound.delivery_receipt_id == "BAE5REMOTE123"
    assert outbound.sent_at.isoformat().startswith("2026-07-06T18:40:42.531")
