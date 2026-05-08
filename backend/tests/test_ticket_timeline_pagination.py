from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/ticket_timeline_pagination_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.api.ticket_perf import (  # noqa: E402
    _encode_timeline_cursor,
    _item_key,
    _parse_cursor,
    _safe_limit,
    get_ticket_timeline_page,
)
from app.db import Base  # noqa: E402
from app.enums import (  # noqa: E402
    EventType,
    MessageStatus,
    NoteVisibility,
    SourceChannel,
    TicketPriority,
    TicketSource,
    TicketStatus,
    UserRole,
)
from app.models import (  # noqa: E402
    Team,
    Ticket,
    TicketAIIntake,
    TicketComment,
    TicketEvent,
    TicketInternalNote,
    TicketOutboundMessage,
    User,
)
from app.webchat_models import WebchatConversation, WebchatEvent  # noqa: E402


def _item(source_type: str, source_id: int, ts: str):
    return {"source_type": source_type, "source_id": source_id, "created_at": ts}


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "ticket_timeline.db"
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


def _team(db, name: str) -> Team:
    row = Team(name=name)
    db.add(row)
    db.flush()
    return row


def _user(db, username: str, role: UserRole, team_id: int | None = None) -> User:
    row = User(
        username=username,
        display_name=username.title(),
        email=f"{username}@example.test",
        password_hash="x",
        role=role,
        team_id=team_id,
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def _ticket(db, ticket_no: str, *, assignee_id: int | None = None, team_id: int | None = None) -> Ticket:
    row = Ticket(
        ticket_no=ticket_no,
        title=ticket_no,
        description="ticket for timeline pagination test",
        source=TicketSource.manual,
        source_channel=SourceChannel.email,
        priority=TicketPriority.medium,
        status=TicketStatus.in_progress,
        assignee_id=assignee_id,
        team_id=team_id,
    )
    db.add(row)
    db.flush()
    return row


def _conversation(db, ticket: Ticket) -> WebchatConversation:
    row = WebchatConversation(
        public_id=f"timeline-conv-{ticket.id}",
        visitor_token_hash=f"timeline-hash-{ticket.id}",
        ticket_id=ticket.id,
    )
    db.add(row)
    db.flush()
    return row


def test_timeline_limit_defaults_and_caps():
    assert _safe_limit(None) == 50
    assert _safe_limit(500) == 100
    assert _safe_limit(1) == 1


def test_timeline_cursor_round_trip_and_sort_key_stable():
    item = _item("comment", 42, "2026-05-07T12:00:00+00:00")
    cursor = _encode_timeline_cursor(item)
    decoded_key = _parse_cursor(cursor)

    assert decoded_key == _item_key(item)
    assert decoded_key[0] == datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)


def test_timeline_invalid_cursor_returns_400():
    with pytest.raises(HTTPException) as exc:
        _parse_cursor("bad")

    assert exc.value.status_code == 400


def test_timeline_sort_order_distinguishes_source_and_id():
    assert _item_key(_item("comment", 1, "2026-05-07T12:00:00+00:00")) != _item_key(_item("internal_note", 2, "2026-05-07T12:00:00+00:00"))


def test_timeline_same_timestamp_comments_paginate_without_duplicates_or_gaps(db_session):
    admin = _user(db_session, "timeline_admin", UserRole.admin)
    ticket = _ticket(db_session, "TIMELINE-1")
    ts = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)
    for idx in range(150):
        db_session.add(
            TicketComment(
                ticket_id=ticket.id,
                author_id=admin.id,
                body=f"comment {idx}",
                visibility=NoteVisibility.external,
                created_at=ts,
            )
        )
    db_session.commit()

    page1 = get_ticket_timeline_page(ticket.id, db=db_session, current_user=admin, limit=50)
    page2 = get_ticket_timeline_page(ticket.id, db=db_session, current_user=admin, cursor=page1["next_cursor"], limit=50)
    page3 = get_ticket_timeline_page(ticket.id, db=db_session, current_user=admin, cursor=page2["next_cursor"], limit=50)

    assert [len(page["items"]) for page in (page1, page2, page3)] == [50, 50, 50]
    assert page1["has_more"] is True
    assert page2["has_more"] is True
    assert page3["has_more"] is False
    assert page3["next_cursor"] is None

    ids = [item["id"] for page in (page1, page2, page3) for item in page["items"]]
    assert len(ids) == 150
    assert len(set(ids)) == 150
    assert ids == [f"comment:{idx}" for idx in range(150, 0, -1)]


def test_timeline_mixed_sources_same_timestamp_sort_stable(db_session):
    admin = _user(db_session, "timeline_admin_mixed", UserRole.admin)
    ticket = _ticket(db_session, "TIMELINE-2")
    conversation = _conversation(db_session, ticket)
    ts = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)

    for idx in range(2):
        db_session.add(TicketComment(ticket_id=ticket.id, author_id=admin.id, body=f"comment {idx}", visibility=NoteVisibility.external, created_at=ts))
        db_session.add(TicketInternalNote(ticket_id=ticket.id, author_id=admin.id, body=f"note {idx}", created_at=ts))
        db_session.add(TicketOutboundMessage(ticket_id=ticket.id, channel=SourceChannel.email, status=MessageStatus.sent, body=f"outbound {idx}", created_by=admin.id, created_at=ts))
        db_session.add(TicketAIIntake(ticket_id=ticket.id, summary=f"ai {idx}", classification="parcel", confidence=0.9, created_by=admin.id, created_at=ts))
        db_session.add(TicketEvent(ticket_id=ticket.id, actor_id=admin.id, event_type=EventType.field_updated, note=f"event {idx}", created_at=ts))
        db_session.add(WebchatEvent(conversation_id=conversation.id, ticket_id=ticket.id, event_type="message.created", payload_json=f'{{"idx": {idx}}}', created_at=ts))
    db_session.commit()

    payload = get_ticket_timeline_page(ticket.id, db=db_session, current_user=admin, limit=20)

    ordered_pairs = [(item["source_type"], item["source_id"]) for item in payload["items"]]
    assert ordered_pairs == [
        ("comment", 2),
        ("comment", 1),
        ("internal_note", 2),
        ("internal_note", 1),
        ("outbound_message", 2),
        ("outbound_message", 1),
        ("ai_intake", 2),
        ("ai_intake", 1),
        ("ticket_event", 2),
        ("ticket_event", 1),
        ("webchat_event", 2),
        ("webchat_event", 1),
    ]
    assert payload["has_more"] is False


def test_timeline_route_invalid_cursor_returns_400(db_session):
    admin = _user(db_session, "timeline_admin_bad_cursor", UserRole.admin)
    ticket = _ticket(db_session, "TIMELINE-3")
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        get_ticket_timeline_page(ticket.id, db=db_session, current_user=admin, cursor="bad", limit=50)

    assert exc.value.status_code == 400


def test_timeline_route_limit_caps_to_100(db_session):
    admin = _user(db_session, "timeline_admin_limit", UserRole.admin)
    ticket = _ticket(db_session, "TIMELINE-4")
    ts = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)
    for idx in range(120):
        db_session.add(TicketComment(ticket_id=ticket.id, author_id=admin.id, body=f"comment {idx}", visibility=NoteVisibility.external, created_at=ts))
    db_session.commit()

    payload = get_ticket_timeline_page(ticket.id, db=db_session, current_user=admin, limit=500)

    assert len(payload["items"]) == 100
    assert payload["has_more"] is True


def test_timeline_invisible_ticket_returns_403(db_session):
    agent = _user(db_session, "timeline_blocked_agent", UserRole.agent)
    ticket = _ticket(db_session, "TIMELINE-5")
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        get_ticket_timeline_page(ticket.id, db=db_session, current_user=agent, limit=50)

    assert exc.value.status_code == 403


def test_timeline_missing_ticket_returns_404(db_session):
    admin = _user(db_session, "timeline_admin_missing", UserRole.admin)
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        get_ticket_timeline_page(999999, db=db_session, current_user=admin, limit=50)

    assert exc.value.status_code == 404
