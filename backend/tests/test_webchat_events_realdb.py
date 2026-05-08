from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_events_realdb_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.api.webchat_events import _hash_token, _list_events  # noqa: E402
from app.db import Base  # noqa: E402
from app.enums import SourceChannel, TicketPriority, TicketSource  # noqa: E402
from app.models import Ticket  # noqa: E402
from app.webchat_models import WebchatConversation, WebchatEvent  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "webchat_events_realdb.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False}, future=True)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def make_ticket(db, ticket_no: str) -> Ticket:
    row = Ticket(
        ticket_no=ticket_no,
        title=ticket_no,
        description=ticket_no,
        source=TicketSource.manual,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
    )
    db.add(row)
    db.flush()
    return row


def make_conversation(db, ticket: Ticket) -> WebchatConversation:
    conversation = WebchatConversation(
        public_id=f"wc-{ticket.id}",
        visitor_token_hash=_hash_token("visitor-token"),
        tenant_key="default",
        channel_key="default",
        ticket_id=ticket.id,
    )
    db.add(conversation)
    db.flush()
    return conversation


def test_list_events_filters_conversation_and_ticket_on_real_db(db_session):
    ticket_a = make_ticket(db_session, "T-A")
    ticket_b = make_ticket(db_session, "T-B")
    conversation_a = make_conversation(db_session, ticket_a)
    conversation_b = make_conversation(db_session, ticket_b)
    for idx, conversation in enumerate([conversation_a, conversation_b, conversation_a], start=1):
        db_session.add(WebchatEvent(
            conversation_id=conversation.id,
            ticket_id=conversation.ticket_id,
            event_type="message.created",
            payload_json=json.dumps({"idx": idx}),
        ))
    db_session.flush()

    events = _list_events(db_session, conversation_id=conversation_a.id, after_id=0, limit=100)

    assert [event["payload_json"]["idx"] for event in events] == [1, 3]
    assert [event["id"] for event in events] == sorted(event["id"] for event in events)


def test_list_events_after_id_and_limit_cap_on_real_db(db_session):
    ticket = make_ticket(db_session, "T-CAP")
    conversation = make_conversation(db_session, ticket)
    for idx in range(125):
        db_session.add(WebchatEvent(
            conversation_id=conversation.id,
            ticket_id=ticket.id,
            event_type="message.created",
            payload_json=json.dumps({"idx": idx}),
        ))
    db_session.flush()

    all_events = _list_events(db_session, conversation_id=conversation.id, after_id=0, limit=500)
    next_events = _list_events(db_session, conversation_id=conversation.id, after_id=all_events[0]["id"], limit=2)

    assert len(all_events) == 100
    assert len(next_events) == 2
    assert next_events[0]["id"] > all_events[0]["id"]
