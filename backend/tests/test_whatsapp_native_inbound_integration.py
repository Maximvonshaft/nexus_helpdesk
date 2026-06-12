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
from app.enums import SourceChannel  # noqa: E402
from app.models import BackgroundJob, ChannelAccount, Ticket, WhatsAppInboundMessage  # noqa: E402
from app.settings import get_settings  # noqa: E402
from app.services.whatsapp_native_inbound import (  # noqa: E402
    WhatsAppNativeAuthError,
    ingest_whatsapp_native_inbound,
    verify_whatsapp_connector_headers,
)
from app.utils.time import utc_now  # noqa: E402
from app.webchat_models import WebchatAITurn, WebchatConversation, WebchatMessage  # noqa: E402


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
        "chat_jid": "41790000000@s.whatsapp.net",
        "sender_jid": "41790000000@s.whatsapp.net",
        "sender_phone": "+41790000000",
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
