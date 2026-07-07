from __future__ import annotations

import os
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_inbox_read_state_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models, operator_models, webchat_models  # noqa: F401,E402
from app.db import Base  # noqa: E402
from app.enums import ConversationState, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.models import Customer, Ticket, User  # noqa: E402
from app.services.webchat_inbox_read_state import mark_webchat_read_state, webchat_read_state_payload  # noqa: E402
from app.webchat_models import WebchatConversation, WebchatEvent  # noqa: E402


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'read_state.db'}", connect_args={"check_same_thread": False}, future=True)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    return engine, Session()


def _fixture_rows(db):
    user = User(username="read-state-admin", display_name="Read State Admin", email="read@example.test", password_hash="x", role=UserRole.admin, is_active=True)
    customer = Customer(name="Read State Visitor", external_ref="read-state-visitor")
    db.add_all([user, customer])
    db.flush()
    ticket = Ticket(
        ticket_no="WC-RS-1",
        title="WebChat read state",
        description="read state",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.in_progress,
        conversation_state=ConversationState.human_owned,
        preferred_reply_channel=SourceChannel.web_chat.value,
    )
    db.add(ticket)
    db.flush()
    conversation = WebchatConversation(public_id="wc_read_state", visitor_token_hash="hash", ticket_id=ticket.id, status="open")
    db.add(conversation)
    db.flush()
    db.add(WebchatEvent(conversation_id=conversation.id, ticket_id=ticket.id, event_type="message.created", payload_json="{}"))
    db.flush()
    return user, ticket, conversation


def test_webchat_inbox_read_state_tracks_manual_unread_and_new_events(tmp_path):
    engine, db = _session(tmp_path)
    try:
        user, ticket, conversation = _fixture_rows(db)

        initial = webchat_read_state_payload(db, conversation_id=conversation.id, user_id=user.id)
        assert initial["unread_count"] == 0
        assert initial["last_read_event_id"] == initial["last_event_id"]

        marked = mark_webchat_read_state(db, ticket_id=ticket.id, current_user=user, marked_unread=True)
        assert marked["marked_unread"] is True
        assert marked["unread_count"] == 1

        read = mark_webchat_read_state(db, ticket_id=ticket.id, current_user=user, marked_unread=False)
        assert read["marked_unread"] is False
        assert read["unread_count"] == 0

        db.add(WebchatEvent(conversation_id=conversation.id, ticket_id=ticket.id, event_type="message.created", payload_json="{}"))
        db.flush()

        changed = webchat_read_state_payload(db, conversation_id=conversation.id, user_id=user.id)
        assert changed["unread_count"] == 1
        assert changed["last_event_id"] > changed["last_read_event_id"]
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
