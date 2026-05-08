from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_events_rbac_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.api.deps import get_current_user  # noqa: E402
from app.api.webchat_events import _hash_token, admin_poll_webchat_events  # noqa: E402
from app.auth_service import create_access_token  # noqa: E402
from app.db import Base  # noqa: E402
from app.enums import SourceChannel, TicketPriority, TicketSource, UserRole  # noqa: E402
from app.models import Team, Ticket, User  # noqa: E402
from app.webchat_models import WebchatConversation, WebchatEvent  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "webchat_events_rbac.db"
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


def make_team(db, name: str) -> Team:
    row = Team(name=name, team_type="support", is_active=True)
    db.add(row)
    db.flush()
    return row


def make_user(db, username: str, role: UserRole, *, team_id: int | None = None, active: bool = True) -> User:
    row = User(
        username=username,
        display_name=username,
        email=f"{username}@example.test",
        password_hash="x",
        role=role,
        team_id=team_id,
        is_active=active,
    )
    db.add(row)
    db.flush()
    return row


def make_ticket(db, ticket_no: str, *, assignee_id: int | None = None, team_id: int | None = None) -> Ticket:
    row = Ticket(
        ticket_no=ticket_no,
        title=ticket_no,
        description=ticket_no,
        source=TicketSource.manual,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        assignee_id=assignee_id,
        team_id=team_id,
    )
    db.add(row)
    db.flush()
    return row


def make_conversation_with_events(db, ticket: Ticket, *, public_id: str = "wc-a", count: int = 3) -> WebchatConversation:
    conversation = WebchatConversation(
        public_id=public_id,
        visitor_token_hash=_hash_token("visitor-token"),
        tenant_key="default",
        channel_key="default",
        ticket_id=ticket.id,
    )
    db.add(conversation)
    db.flush()
    for i in range(count):
        db.add(WebchatEvent(
            conversation_id=conversation.id,
            ticket_id=ticket.id,
            event_type="message.created",
            payload_json=json.dumps({"seq": i}),
        ))
    db.flush()
    return conversation


def test_unauthenticated_admin_events_auth_dependency_returns_401(db_session):
    with pytest.raises(HTTPException) as exc:
        get_current_user(credentials=None, x_user_id=None, db=db_session)

    assert exc.value.status_code == 401


def test_agent_reads_own_ticket_events(db_session):
    agent = make_user(db_session, "agent", UserRole.agent)
    ticket = make_ticket(db_session, "T-OWN", assignee_id=agent.id)
    make_conversation_with_events(db_session, ticket)

    response = admin_poll_webchat_events(ticket.id, after_id=0, limit=50, wait_ms=0, db=db_session, current_user=agent)

    assert len(response["events"]) == 3
    assert response["events"][0]["id"] < response["events"][-1]["id"]


def test_agent_cannot_read_other_team_ticket_events(db_session):
    team_a = make_team(db_session, "team-a")
    team_b = make_team(db_session, "team-b")
    agent = make_user(db_session, "agent-a", UserRole.agent, team_id=team_a.id)
    ticket = make_ticket(db_session, "T-OTHER", team_id=team_b.id)
    make_conversation_with_events(db_session, ticket)

    with pytest.raises(HTTPException) as exc:
        admin_poll_webchat_events(ticket.id, after_id=0, limit=50, wait_ms=0, db=db_session, current_user=agent)

    assert exc.value.status_code == 403


def test_admin_reads_any_ticket_events(db_session):
    admin = make_user(db_session, "admin", UserRole.admin)
    ticket = make_ticket(db_session, "T-ANY")
    make_conversation_with_events(db_session, ticket)

    response = admin_poll_webchat_events(ticket.id, after_id=0, limit=50, wait_ms=0, db=db_session, current_user=admin)

    assert len(response["events"]) == 3


def test_inactive_user_token_returns_401(db_session):
    user = make_user(db_session, "inactive", UserRole.admin, active=False)
    token = create_access_token(user.id)
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    with pytest.raises(HTTPException) as exc:
        get_current_user(credentials=credentials, x_user_id=None, db=db_session)

    assert exc.value.status_code == 401


def test_events_order_is_id_asc_and_limit_caps_to_100(db_session):
    admin = make_user(db_session, "admin2", UserRole.admin)
    ticket = make_ticket(db_session, "T-LIMIT")
    make_conversation_with_events(db_session, ticket, public_id="wc-limit", count=150)

    response = admin_poll_webchat_events(ticket.id, after_id=0, limit=500, wait_ms=0, db=db_session, current_user=admin)
    ids = [item["id"] for item in response["events"]]

    assert len(ids) == 100
    assert ids == sorted(ids)
