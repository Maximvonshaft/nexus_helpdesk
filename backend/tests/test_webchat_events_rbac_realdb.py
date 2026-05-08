from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_events_rbac_realdb_tests.db")
os.environ.setdefault("WEBCHAT_RATE_LIMIT_BACKEND", "memory")
os.environ.setdefault("WEBCHAT_ALLOW_NO_ORIGIN", "true")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.api.deps import get_current_user  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.enums import SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Customer, Team, Ticket, User  # noqa: E402
from app.webchat_models import WebchatConversation, WebchatEvent  # noqa: E402


@pytest.fixture()
def api_context(tmp_path):
    db_file = tmp_path / "webchat_events_rbac_realdb.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False}, future=True)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = Session()
    state = {"user": None}

    def override_db():
        yield session

    def override_current_user():
        return state["user"]

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_current_user
    client = TestClient(app)
    try:
        yield session, client, state
    finally:
        app.dependency_overrides.clear()
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def make_team(db, name: str) -> Team:
    row = Team(name=name)
    db.add(row)
    db.flush()
    return row


def make_user(db, username: str, role: UserRole = UserRole.agent, team_id: int | None = None) -> User:
    row = User(
        username=username,
        display_name=username,
        email=f"{username}@invalid.test",
        password_hash="x",
        role=role,
        team_id=team_id,
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def make_ticket(db, ticket_no: str, *, assignee_id: int | None = None, team_id: int | None = None) -> Ticket:
    customer = Customer(name=f"Customer {ticket_no}", email=f"{ticket_no.lower()}@invalid.test")
    db.add(customer)
    db.flush()
    row = Ticket(
        ticket_no=ticket_no,
        title=f"Ticket {ticket_no}",
        description="RBAC test ticket",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.in_progress,
        assignee_id=assignee_id,
        team_id=team_id,
    )
    db.add(row)
    db.flush()
    return row


def make_conversation(db, ticket: Ticket, public_id: str) -> WebchatConversation:
    row = WebchatConversation(
        public_id=public_id,
        visitor_token_hash=hashlib.sha256(b"visitor-token").hexdigest(),
        tenant_key="default",
        channel_key="default",
        ticket_id=ticket.id,
        visitor_name="Visitor",
        visitor_email="visitor@invalid.test",
        status="open",
    )
    db.add(row)
    db.flush()
    return row


def add_event(db, conversation: WebchatConversation, event_type: str) -> WebchatEvent:
    row = WebchatEvent(
        conversation_id=conversation.id,
        ticket_id=conversation.ticket_id,
        event_type=event_type,
        payload_json='{"ok": true}',
    )
    db.add(row)
    db.flush()
    return row


def test_admin_events_rbac_real_api_and_pagination(api_context):
    db, client, state = api_context
    team_a = make_team(db, "team-a")
    team_b = make_team(db, "team-b")
    admin = make_user(db, "admin", UserRole.admin)
    agent = make_user(db, "agent-a", UserRole.agent, team_id=team_a.id)
    visible_ticket = make_ticket(db, "WC-RBAC-1", assignee_id=agent.id, team_id=team_a.id)
    hidden_ticket = make_ticket(db, "WC-RBAC-2", team_id=team_b.id)
    visible_conversation = make_conversation(db, visible_ticket, "wc_visible")
    hidden_conversation = make_conversation(db, hidden_ticket, "wc_hidden")
    visible_events = [add_event(db, visible_conversation, f"visible.{idx}") for idx in range(3)]
    hidden_event = add_event(db, hidden_conversation, "hidden.must_not_leak")
    db.commit()

    state["user"] = admin
    first = client.get(f"/api/webchat/admin/tickets/{visible_ticket.id}/events", params={"limit": 2})
    assert first.status_code == 200
    first_payload = first.json()
    assert [event["id"] for event in first_payload["events"]] == [visible_events[0].id, visible_events[1].id]
    assert first_payload["last_event_id"] == visible_events[1].id
    assert first_payload["has_more"] is True

    second = client.get(
        f"/api/webchat/admin/tickets/{visible_ticket.id}/events",
        params={"after_id": visible_events[1].id, "limit": 2},
    )
    assert second.status_code == 200
    second_payload = second.json()
    assert [event["id"] for event in second_payload["events"]] == [visible_events[2].id]
    assert second_payload["last_event_id"] == visible_events[2].id
    assert second_payload["has_more"] is False

    admin_hidden = client.get(f"/api/webchat/admin/tickets/{hidden_ticket.id}/events")
    assert admin_hidden.status_code == 200
    assert [event["id"] for event in admin_hidden.json()["events"]] == [hidden_event.id]

    state["user"] = agent
    agent_visible = client.get(f"/api/webchat/admin/tickets/{visible_ticket.id}/events")
    assert agent_visible.status_code == 200
    assert {event["id"] for event in agent_visible.json()["events"]} == {event.id for event in visible_events}

    agent_hidden = client.get(f"/api/webchat/admin/tickets/{hidden_ticket.id}/events")
    assert agent_hidden.status_code == 403
    assert "hidden.must_not_leak" not in agent_hidden.text

    missing = client.get("/api/webchat/admin/tickets/999999/events")
    assert missing.status_code == 404
