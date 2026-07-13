from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/ticket_event_external_comment_classification.db",
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models, operator_models, tool_models, voice_models, webchat_models  # noqa: E402,F401
from app.db import Base  # noqa: E402
from app.enums import (  # noqa: E402
    ConversationState,
    EventType,
    NoteVisibility,
    SourceChannel,
    TicketPriority,
    TicketSource,
    TicketStatus,
    UserRole,
)
from app.models import (  # noqa: E402
    ChannelAccount,
    Customer,
    Ticket,
    TicketComment,
    TicketEvent,
    User,
)
from app.schemas import CommentCreate  # noqa: E402
from app.services import ticket_service, whatsapp_native_inbound  # noqa: E402
from app.services.ticket_event_writer import TicketEventClass  # noqa: E402
from app.services.webchat_service import add_visitor_message, submit_card_action  # noqa: E402
from app.webchat_models import WebchatCardAction, WebchatConversation, WebchatMessage  # noqa: E402
from app.webchat_schemas import WebChatActionSubmitRequest  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    database_path = tmp_path / "ticket_event_external_comment.db"
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        future=True,
        expire_on_commit=False,
    )
    Base.metadata.create_all(engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/api/webchat/messages",
            "raw_path": b"/api/webchat/messages",
            "query_string": b"",
            "headers": [(b"origin", b"https://example.test")],
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 443),
        }
    )


def _make_ticket_and_conversation(db):
    visitor_token = "server-issued-visitor-token"
    customer = Customer(
        name="External Visitor",
        external_ref="external-comment-classification",
    )
    db.add(customer)
    db.flush()
    ticket = Ticket(
        ticket_no=f"EXT-{customer.id}",
        title="External visitor comment classification",
        description="classification regression",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.pending_assignment,
        conversation_state=ConversationState.ai_active,
    )
    db.add(ticket)
    db.flush()
    conversation = WebchatConversation(
        public_id=f"wc_external_{ticket.id}",
        visitor_token_hash=hashlib.sha256(
            visitor_token.encode("utf-8")
        ).hexdigest(),
        tenant_key="pytest",
        channel_key="website",
        ticket_id=ticket.id,
        visitor_name="External Visitor",
        status="open",
    )
    db.add(conversation)
    db.flush()
    return ticket, conversation, visitor_token


def _payload(row: TicketEvent) -> dict:
    return json.loads(row.payload_json or "{}")


def _comment_event(
    db,
    *,
    ticket_id: int,
    actor_id: int | None = None,
) -> TicketEvent:
    query = db.query(TicketEvent).filter(
        TicketEvent.ticket_id == ticket_id,
        TicketEvent.event_type == EventType.comment_added,
    )
    query = (
        query.filter(TicketEvent.actor_id.is_(None))
        if actor_id is None
        else query.filter(TicketEvent.actor_id == actor_id)
    )
    return query.one()


def _external_comment(db, ticket_id: int) -> TicketComment:
    return (
        db.query(TicketComment)
        .filter(
            TicketComment.ticket_id == ticket_id,
            TicketComment.visibility == NoteVisibility.external,
        )
        .one()
    )


def test_public_webchat_visitor_creates_customer_visible_comment_event_without_body(
    db_session,
):
    ticket, conversation, visitor_token = _make_ticket_and_conversation(
        db_session
    )
    body_marker = "RAW-VISITOR-BODY-MUST-NOT-ENTER-TICKET-EVENT"

    response = add_visitor_message(
        db_session,
        conversation.public_id,
        visitor_token,
        body_marker,
        _request(),
    )

    visitor_message = (
        db_session.query(WebchatMessage)
        .filter(
            WebchatMessage.id == response["message"]["id"],
            WebchatMessage.direction == "visitor",
        )
        .one()
    )
    external_comment = _external_comment(db_session, ticket.id)
    event = _comment_event(db_session, ticket_id=ticket.id)
    payload = _payload(event)

    assert external_comment.body == body_marker
    assert payload["event_class"] == TicketEventClass.CUSTOMER_VISIBLE.value
    assert payload["conversation_id"] == conversation.id
    assert payload["comment_id"] == external_comment.id
    assert payload["webchat_message_id"] == visitor_message.id
    assert body_marker not in (event.payload_json or "")
    assert "public_conversation_id" not in payload
    assert "client_message_id" not in payload


def test_public_webchat_card_action_uses_external_comment_authority_without_payload_text(
    db_session,
):
    ticket, conversation, visitor_token = _make_ticket_and_conversation(
        db_session
    )
    label_marker = "CUSTOMER-CONTROLLED-CARD-LABEL"
    card_payload = {
        "card_id": "address-card-1",
        "card_type": "address_confirmation",
        "version": 1,
        "title": "Confirm address",
        "body": "Please confirm the address.",
        "actions": [
            {
                "id": "confirm-address",
                "label": label_marker,
                "value": "customer-controlled-value",
                "action_type": "address_confirm",
                "payload": {"customer_note": "must-not-enter-audit"},
            }
        ],
        "metadata": {},
    }
    card_message = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        direction="agent",
        body="Confirm address",
        body_text="Confirm address",
        message_type="card",
        payload_json=json.dumps(card_payload),
        delivery_status="sent",
        author_label="Nexus",
    )
    db_session.add(card_message)
    db_session.flush()

    response = submit_card_action(
        db_session,
        conversation.public_id,
        visitor_token,
        WebChatActionSubmitRequest(
            visitor_token=visitor_token,
            message_id=card_message.id,
            card_id="address-card-1",
            action_id="confirm-address",
            action_type="address_confirm",
            payload={},
        ),
        _request(),
    )

    action = (
        db_session.query(WebchatCardAction)
        .filter(WebchatCardAction.id == response["action_id"])
        .one()
    )
    action_message = (
        db_session.query(WebchatMessage)
        .filter(
            WebchatMessage.conversation_id == conversation.id,
            WebchatMessage.direction == "action",
        )
        .one()
    )
    external_comment = _external_comment(db_session, ticket.id)
    event = _comment_event(db_session, ticket_id=ticket.id)
    payload = _payload(event)

    assert action_message.ticket_id == ticket.id
    assert payload["event_class"] == TicketEventClass.CUSTOMER_VISIBLE.value
    assert payload["conversation_id"] == conversation.id
    assert payload["comment_id"] == external_comment.id
    assert payload["webchat_card_action_id"] == action.id
    assert "webchat_message_id" not in payload
    assert label_marker not in (event.payload_json or "")
    assert "customer-controlled-value" not in (event.payload_json or "")
    assert "must-not-enter-audit" not in (event.payload_json or "")


def test_whatsapp_native_inbound_external_comment_uses_customer_visible_event(
    db_session,
    monkeypatch,
):
    account = ChannelAccount(
        provider="whatsapp",
        account_id="wa-main",
        display_name="WhatsApp Main",
        is_active=True,
        priority=10,
    )
    db_session.add(account)
    db_session.flush()
    monkeypatch.setattr(
        whatsapp_native_inbound,
        "_schedule_ai_turn",
        lambda *args, **kwargs: {"ai_turn_id": None, "ai_status": None},
    )
    body_marker = "RAW-WHATSAPP-BODY-MUST-NOT-ENTER-TICKET-EVENT"

    result = whatsapp_native_inbound.ingest_whatsapp_native_inbound(
        db_session,
        {
            "account_id": account.account_id,
            "external_message_id": "wa-external-message-1",
            "chat_jid": "15551234567@s.whatsapp.net",
            "sender_jid": "15551234567@s.whatsapp.net",
            "sender_phone": "+15551234567",
            "message_type": "text",
            "body_text": body_marker,
            "received_at": "2026-07-13T08:00:00Z",
            "from_me": False,
        },
    )

    external_comment = _external_comment(db_session, result.ticket_id)
    event = _comment_event(db_session, ticket_id=result.ticket_id)
    payload = _payload(event)

    assert payload["event_class"] == TicketEventClass.CUSTOMER_VISIBLE.value
    assert payload["conversation_id"] == result.conversation_id
    assert payload["comment_id"] == external_comment.id
    assert payload["webchat_message_id"] == result.webchat_message_id
    assert payload["whatsapp_inbound_message_id"] == result.inbound_message_id
    assert body_marker not in (event.payload_json or "")
    assert "chat_jid" not in payload
    assert "external_message_id" not in payload


def test_agent_internal_comment_remains_internal_audit(
    db_session,
    monkeypatch,
):
    ticket, _conversation, _visitor_token = _make_ticket_and_conversation(
        db_session
    )
    agent = User(
        username="internal_comment_agent",
        display_name="Internal Comment Agent",
        email="internal-comment-agent@example.test",
        password_hash="x",
        role=UserRole.admin,
        is_active=True,
    )
    db_session.add(agent)
    db_session.flush()
    monkeypatch.setattr(
        ticket_service,
        "ensure_ticket_visible",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        ticket_service,
        "ensure_can_write_comment",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        ticket_service,
        "evaluate_sla",
        lambda *args, **kwargs: None,
    )
    body_marker = "RAW-INTERNAL-COMMENT-MUST-NOT-ENTER-TICKET-EVENT"

    internal_comment = ticket_service.add_comment(
        db_session,
        ticket.id,
        CommentCreate(
            body=body_marker,
            visibility=NoteVisibility.internal,
        ),
        agent,
    )

    event = _comment_event(
        db_session,
        ticket_id=ticket.id,
        actor_id=agent.id,
    )
    payload = _payload(event)

    assert internal_comment.visibility == NoteVisibility.internal
    assert payload["event_class"] == TicketEventClass.INTERNAL_AUDIT.value
    assert body_marker not in (event.payload_json or "")
