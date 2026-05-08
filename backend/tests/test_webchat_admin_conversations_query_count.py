from __future__ import annotations

import hashlib
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_admin_conversations_tests.db")
os.environ.setdefault("WEBCHAT_RATE_LIMIT_BACKEND", "memory")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.api.deps import get_current_user  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.enums import ConversationState, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Customer, Team, Ticket, User  # noqa: E402
from app.webchat_models import WebchatConversation, WebchatMessage  # noqa: E402


@pytest.fixture()
def api_context(tmp_path):
    db_file = tmp_path / "webchat_admin_conversations.db"
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
        yield session, client, state, engine
    finally:
        app.dependency_overrides.clear()
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@contextmanager
def query_counter(engine):
    count = {"value": 0}

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        sql = str(statement).lstrip().upper()
        if sql.startswith(("PRAGMA", "SAVEPOINT", "RELEASE", "ROLLBACK TO")):
            return
        count["value"] += 1

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        yield count
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)


def make_team(db, name: str) -> Team:
    row = Team(name=name)
    db.add(row)
    db.flush()
    return row


def make_user(db, username: str, role: UserRole, team_id: int | None = None) -> User:
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


def make_conversation_fixture(db, *, idx: int, team_id: int, assignee_id: int | None = None) -> tuple[Ticket, WebchatConversation]:
    customer = Customer(name=f"Visitor {idx}", email=f"visitor{idx}@invalid.test", phone=f"+41000{idx:03d}")
    db.add(customer)
    db.flush()
    ticket = Ticket(
        ticket_no=f"WC-LIST-{idx:03d}",
        title=f"Conversation ticket {idx}",
        description="conversation list fixture",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.in_progress,
        team_id=team_id,
        assignee_id=assignee_id,
        required_action="human review" if idx == 0 else None,
        conversation_state=ConversationState.human_review_required if idx == 0 else ConversationState.human_owned,
    )
    db.add(ticket)
    db.flush()
    now = datetime(2026, 5, 7, 12, 0, 0) + timedelta(seconds=idx)
    conversation = WebchatConversation(
        public_id=f"wc_{idx:03d}",
        visitor_token_hash=hashlib.sha256(f"visitor-token-{idx}".encode()).hexdigest(),
        tenant_key="default",
        channel_key="default",
        ticket_id=ticket.id,
        visitor_name=customer.name,
        visitor_email=customer.email,
        visitor_phone=customer.phone,
        status="open",
        active_ai_turn_id=9000 + idx if idx == 0 else None,
        active_ai_status="queued" if idx == 0 else None,
        active_ai_for_message_id=8000 + idx if idx == 0 else None,
        updated_at=now,
        last_seen_at=now,
    )
    db.add(conversation)
    db.flush()
    db.add(
        WebchatMessage(
            conversation_id=conversation.id,
            ticket_id=ticket.id,
            direction="visitor",
            body=f"hello {idx}",
            body_text=f"hello {idx}",
            message_type="text",
            delivery_status="sent",
            author_label=customer.name,
            created_at=now,
        )
    )
    db.flush()
    return ticket, conversation


def test_admin_conversations_query_count_and_contract(api_context):
    db, client, state, engine = api_context
    team_a = make_team(db, "team-a")
    team_b = make_team(db, "team-b")
    admin = make_user(db, "admin", UserRole.admin)
    agent = make_user(db, "agent-a", UserRole.agent, team_id=team_a.id)
    visible_ticket_ids = set()
    hidden_ticket_ids = set()
    for idx in range(50):
        team_id = team_a.id if idx % 2 == 0 else team_b.id
        assignee_id = agent.id if idx % 4 == 0 else None
        ticket, _ = make_conversation_fixture(db, idx=idx, team_id=team_id, assignee_id=assignee_id)
        if team_id == team_a.id or assignee_id == agent.id:
            visible_ticket_ids.add(ticket.id)
        else:
            hidden_ticket_ids.add(ticket.id)
    db.commit()

    state["user"] = admin
    with query_counter(engine) as queries:
        response = client.get("/api/webchat/admin/conversations", params={"limit": 50})
    assert response.status_code == 200
    assert queries["value"] <= 8

    payload = response.json()
    assert len(payload) == 50
    required_fields = {
        "conversation_id",
        "ticket_id",
        "ticket_no",
        "title",
        "status",
        "visitor_name",
        "visitor_email",
        "visitor_phone",
        "updated_at",
        "last_message_type",
        "needs_human",
        "ai_status",
    }
    assert required_fields.issubset(payload[0].keys())
    ai_row = next(item for item in payload if item["conversation_id"] == "wc_000")
    assert ai_row["needs_human"] is True
    assert ai_row["ai_status"] == "queued"
    assert ai_row["last_message_type"] == "text"

    state["user"] = agent
    agent_response = client.get("/api/webchat/admin/conversations", params={"limit": 50})
    assert agent_response.status_code == 200
    agent_items = agent_response.json()
    assert {item["ticket_id"] for item in agent_items}.issubset(visible_ticket_ids)
    assert not ({item["ticket_id"] for item in agent_items} & hidden_ticket_ids)
