import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/helpdesk_suite_whatsapp_webchat_bridge.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models, operator_models, webchat_models  # noqa: F401,E402
from app.db import Base  # noqa: E402
from app.enums import ConversationState, MessageStatus, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.models import ChannelAccount, OpenClawConversationLink, Ticket, TicketOutboundMessage, User  # noqa: E402
from app.services import message_dispatch, openclaw_bridge  # noqa: E402
from app.services.outbound_adapters import whatsapp as whatsapp_adapter  # noqa: E402
from app.services.background_jobs import WEBCHAT_AI_REPLY_JOB  # noqa: E402
from app.services.webchat_service import admin_reply  # noqa: E402
from app.utils.time import utc_now  # noqa: E402
from app.webchat_models import WebchatAITurn, WebchatConversation, WebchatHandoffRequest, WebchatMessage  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "suite.db"
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


def _ticket(db, *, contact: str = "+15550001111") -> Ticket:
    row = Ticket(
        ticket_no=f"T-WA-{contact[-4:]}",
        title="WhatsApp customer",
        description="WhatsApp inbound",
        source=TicketSource.user_message,
        source_channel=SourceChannel.whatsapp,
        priority=TicketPriority.medium,
        status=TicketStatus.pending_assignment,
        conversation_state=ConversationState.ai_active,
        source_chat_id=contact,
        preferred_reply_channel=SourceChannel.whatsapp.value,
        preferred_reply_contact=contact,
    )
    db.add(row)
    db.flush()
    return row


def _admin(db) -> User:
    user = User(
        username="admin-wa",
        display_name="Admin WA",
        email="admin-wa@example.test",
        password_hash="x",
        role=UserRole.admin,
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user


def _account(db) -> ChannelAccount:
    row = ChannelAccount(provider="whatsapp", account_id="wa-main", display_name="WhatsApp Main", is_active=True, priority=1)
    db.add(row)
    db.flush()
    return row


def _sync_payload(text: str):
    return (
        {"sessionKey": "sess-wa-1", "route": {"channel": "whatsapp", "recipient": "+15550001111", "accountId": "wa-main"}},
        [{"id": "msg-wa-1", "role": "user", "author": "customer", "text": text, "createdAt": "2026-06-10T10:00:00+00:00"}],
    )


def test_whatsapp_openclaw_inbound_projects_to_unified_webchat_and_schedules_ai(db_session, monkeypatch):
    ticket = _ticket(db_session)
    account = _account(db_session)
    monkeypatch.setattr(openclaw_bridge.settings, "openclaw_bridge_enabled", True)
    monkeypatch.setattr(openclaw_bridge, "read_openclaw_bridge_conversation", lambda *args, **kwargs: _sync_payload("Hello, I need help"))
    monkeypatch.setattr(openclaw_bridge, "fetch_openclaw_bridge_attachments", lambda *args, **kwargs: [])

    openclaw_bridge.sync_openclaw_conversation(db_session, ticket_id=ticket.id, session_key="sess-wa-1", limit=10)
    db_session.flush()

    link = db_session.query(OpenClawConversationLink).filter_by(session_key="sess-wa-1").one()
    conversation = db_session.query(WebchatConversation).filter_by(ticket_id=ticket.id, channel_key="whatsapp").one()
    message = db_session.query(WebchatMessage).filter_by(conversation_id=conversation.id, direction="visitor").one()
    turn = db_session.query(WebchatAITurn).filter_by(conversation_id=conversation.id).one()

    assert link.channel_account_id == account.id
    assert conversation.origin == "openclaw-whatsapp"
    assert message.body == "Hello, I need help"
    assert turn.status == "queued"
    assert conversation.active_ai_turn_id == turn.id
    assert db_session.query(models.BackgroundJob).filter_by(job_type=WEBCHAT_AI_REPLY_JOB, dedupe_key=f"webchat-ai-turn:{turn.id}").count() == 1


def test_whatsapp_high_risk_inbound_requests_handoff_and_queues_ack(db_session, monkeypatch):
    ticket = _ticket(db_session)
    _account(db_session)
    monkeypatch.setattr(openclaw_bridge.settings, "openclaw_bridge_enabled", True)
    monkeypatch.setattr(openclaw_bridge, "read_openclaw_bridge_conversation", lambda *args, **kwargs: _sync_payload("I want a refund and compensation"))
    monkeypatch.setattr(openclaw_bridge, "fetch_openclaw_bridge_attachments", lambda *args, **kwargs: [])

    openclaw_bridge.sync_openclaw_conversation(db_session, ticket_id=ticket.id, session_key="sess-wa-1", limit=10)
    db_session.flush()

    conversation = db_session.query(WebchatConversation).filter_by(ticket_id=ticket.id, channel_key="whatsapp").one()
    handoff = db_session.query(WebchatHandoffRequest).filter_by(conversation_id=conversation.id).one()
    outbound = db_session.query(TicketOutboundMessage).filter_by(ticket_id=ticket.id, channel=SourceChannel.whatsapp).one()

    assert handoff.status == "requested"
    assert conversation.ai_suspended is True
    assert outbound.status == MessageStatus.pending
    assert outbound.provider_status == "whatsapp_handoff_ack"
    assert db_session.query(WebchatAITurn).filter_by(conversation_id=conversation.id).count() == 0


def test_admin_reply_on_whatsapp_unified_conversation_queues_whatsapp_outbound(db_session):
    admin = _admin(db_session)
    ticket = _ticket(db_session)
    account = _account(db_session)
    link = OpenClawConversationLink(
        ticket_id=ticket.id,
        session_key="sess-wa-admin",
        channel="whatsapp",
        recipient="+15550001111",
        account_id=account.account_id,
        channel_account_id=account.id,
    )
    db_session.add(link)
    conversation = WebchatConversation(
        public_id="wa_admin_reply",
        visitor_token_hash="hash",
        tenant_key="openclaw",
        channel_key="whatsapp",
        ticket_id=ticket.id,
        visitor_name="WhatsApp Customer",
        visitor_phone="+15550001111",
        visitor_ref="sess-wa-admin",
        origin="openclaw-whatsapp",
        status="open",
    )
    db_session.add(conversation)
    db_session.flush()

    result = admin_reply(db_session, ticket.id, admin, body="Hello, I will check this for you.")
    outbound = db_session.query(TicketOutboundMessage).filter_by(ticket_id=ticket.id, channel=SourceChannel.whatsapp).one()
    message = db_session.query(WebchatMessage).filter_by(conversation_id=conversation.id, direction="agent").one()

    assert result["ok"] is True
    assert message.delivery_status == "queued"
    assert outbound.status == MessageStatus.pending
    assert outbound.provider_status == "whatsapp_agent_reply"
    assert outbound.body == "Hello, I will check this for you."


def test_admin_reply_whatsapp_outbox_worker_claims_and_dispatches_sent(db_session, monkeypatch):
    admin = _admin(db_session)
    ticket = _ticket(db_session)
    account = _account(db_session)
    link = OpenClawConversationLink(
        ticket_id=ticket.id,
        session_key="sess-wa-worker",
        channel="whatsapp",
        recipient="+15550001111",
        account_id=account.account_id,
        channel_account_id=account.id,
    )
    db_session.add(link)
    conversation = WebchatConversation(
        public_id="wa_worker_reply",
        visitor_token_hash="hash-worker",
        tenant_key="openclaw",
        channel_key="whatsapp",
        ticket_id=ticket.id,
        visitor_name="WhatsApp Customer",
        visitor_phone="+15550001111",
        visitor_ref="sess-wa-worker",
        origin="openclaw-whatsapp",
        status="open",
    )
    db_session.add(conversation)
    db_session.flush()

    monkeypatch.setattr(message_dispatch.settings, "enable_outbound_dispatch", True)
    monkeypatch.setattr(message_dispatch.settings, "outbound_provider", "openclaw")
    bridge_calls = []

    def fake_openclaw_bridge_dispatch(**kwargs):
        bridge_calls.append(kwargs)
        return MessageStatus.sent, "sent_via_worker_smoke", utc_now()

    monkeypatch.setattr(whatsapp_adapter, "dispatch_via_openclaw_bridge", fake_openclaw_bridge_dispatch)

    result = admin_reply(db_session, ticket.id, admin, body="Hello, I will check this for you.")
    outbound = db_session.query(TicketOutboundMessage).filter_by(ticket_id=ticket.id, channel=SourceChannel.whatsapp).one()
    assert result["ok"] is True
    assert outbound.status == MessageStatus.pending

    db_session.commit()
    claimed = message_dispatch.claim_pending_messages(db_session, worker_id="worker-whatsapp-smoke")
    assert [row.id for row in claimed] == [outbound.id]

    processed = message_dispatch.process_outbound_message(db_session, claimed[0])
    db_session.commit()

    assert processed.status == MessageStatus.sent
    assert processed.provider_status == "sent_via_worker_smoke"
    assert processed.sent_at is not None
    assert ticket.conversation_state == ConversationState.waiting_customer
    assert bridge_calls == [{
        "channel": "whatsapp",
        "target": "+15550001111",
        "body": "Hello, I will check this for you.",
        "account_id": "wa-main",
        "thread_id": None,
        "session_key": "sess-wa-worker",
    }]
