from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_events_realdb_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.api.webchat_events import admin_poll_webchat_events  # noqa: E402
from app.db import Base  # noqa: E402
from app.enums import SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.models import Team, Ticket, User  # noqa: E402
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
        description="ticket for webchat events RBAC test",
        source=TicketSource.manual,
        source_channel=SourceChannel.web_chat,
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
        public_id=f"conv-{ticket.id}",
        visitor_token_hash=f"hash-{ticket.id}",
        ticket_id=ticket.id,
    )
    db.add(row)
    db.flush()
    return row


def _events(db, ticket: Ticket, count: int) -> list[WebchatEvent]:
    conversation = _conversation(db, ticket)
    rows: list[WebchatEvent] = []
    for idx in range(count):
        row = WebchatEvent(
            conversation_id=conversation.id,
            ticket_id=ticket.id,
            event_type="message.created",
            payload_json=f'{{"idx": {idx}}}',
        )
        db.add(row)
        db.flush()
        rows.append(row)
    db.commit()
    return rows


def test_admin_can_read_any_ticket_events_and_pagination_shape(db_session):
    admin = _user(db_session, "admin", UserRole.admin)
    ticket = _ticket(db_session, "WEBCHAT-EVENTS-1")
    rows = _events(db_session, ticket, 3)

    payload = admin_poll_webchat_events(ticket.id, after_id=0, limit=2, wait_ms=25000, db=db_session, current_user=admin)

    assert [event["id"] for event in payload["events"]] == [rows[0].id, rows[1].id]
    assert payload["last_event_id"] == rows[1].id
    assert payload["has_more"] is True
    assert payload["wait_ms"] <= 5000

    second = admin_poll_webchat_events(ticket.id, after_id=rows[1].id, limit=2, wait_ms=0, db=db_session, current_user=admin)
    assert [event["id"] for event in second["events"]] == [rows[2].id]
    assert second["has_more"] is False


def test_agent_can_read_assigned_ticket_events(db_session):
    agent = _user(db_session, "agent_assigned", UserRole.agent)
    ticket = _ticket(db_session, "WEBCHAT-EVENTS-2", assignee_id=agent.id)
    rows = _events(db_session, ticket, 1)

    payload = admin_poll_webchat_events(ticket.id, after_id=0, limit=50, wait_ms=0, db=db_session, current_user=agent)

    assert [event["id"] for event in payload["events"]] == [rows[0].id]


def test_agent_can_read_team_visible_ticket_events(db_session):
    team = _team(db_session, "Support Team")
    agent = _user(db_session, "agent_team", UserRole.agent, team_id=team.id)
    ticket = _ticket(db_session, "WEBCHAT-EVENTS-3", team_id=team.id)
    rows = _events(db_session, ticket, 1)

    payload = admin_poll_webchat_events(ticket.id, after_id=0, limit=50, wait_ms=0, db=db_session, current_user=agent)

    assert [event["id"] for event in payload["events"]] == [rows[0].id]


def test_agent_cannot_read_invisible_ticket_events_even_when_events_exist(db_session):
    agent = _user(db_session, "agent_blocked", UserRole.agent)
    ticket = _ticket(db_session, "WEBCHAT-EVENTS-4")
    _events(db_session, ticket, 2)

    with pytest.raises(HTTPException) as exc:
        admin_poll_webchat_events(ticket.id, after_id=0, limit=50, wait_ms=0, db=db_session, current_user=agent)

    assert exc.value.status_code == 403


def test_admin_ticket_events_missing_ticket_returns_404(db_session):
    admin = _user(db_session, "admin_missing", UserRole.admin)

    with pytest.raises(HTTPException) as exc:
        admin_poll_webchat_events(999999, after_id=0, limit=50, wait_ms=0, db=db_session, current_user=admin)

    assert exc.value.status_code == 404
