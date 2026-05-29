from __future__ import annotations

import os
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models as _models  # noqa: E402,F401
from app import webchat_models as _webchat_models  # noqa: E402,F401
from app.auth_service import create_access_token  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.enums import ConversationState, ResolutionCategory, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Customer, Team, Ticket, User, UserCapabilityOverride  # noqa: E402
from app.settings import get_settings  # noqa: E402
from app.utils.time import utc_now  # noqa: E402
from app.webchat_models import WebchatConversation, WebchatHandoffRequest  # noqa: E402


@pytest.fixture()
def db_session(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLOW_DEV_AUTH", "false")
    get_settings.cache_clear()
    db_file = tmp_path / "today_workbench.db"
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
        get_settings.cache_clear()


@pytest.fixture()
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _suffix() -> str:
    return uuid.uuid4().hex[:10]


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def _team(db_session, name: str) -> Team:
    row = Team(name=f"{name}-{_suffix()}", team_type="support")
    db_session.add(row)
    db_session.flush()
    return row


def _user(db_session, username: str, role: UserRole, team: Team | None = None) -> User:
    row = User(
        username=f"{username}-{_suffix()}",
        display_name=username.title(),
        email=f"{username}-{_suffix()}@example.test",
        password_hash="test",
        role=role,
        team_id=team.id if team else None,
        is_active=True,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _ticket(
    db_session,
    *,
    ticket_no: str,
    team: Team,
    assignee: User | None = None,
    status: TicketStatus = TicketStatus.in_progress,
    source_channel: SourceChannel = SourceChannel.whatsapp,
    priority: TicketPriority = TicketPriority.medium,
    first_response_due_at=None,
    resolution_due_at=None,
    conversation_state: ConversationState = ConversationState.ai_active,
) -> Ticket:
    customer = Customer(name=f"Customer {ticket_no}", email=f"{ticket_no.lower()}@example.test")
    db_session.add(customer)
    db_session.flush()
    row = Ticket(
        ticket_no=f"{ticket_no}-{_suffix()}",
        title=f"{ticket_no} customer request",
        description=f"{ticket_no} description",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=source_channel,
        priority=priority,
        status=status,
        resolution_category=ResolutionCategory.none,
        team_id=team.id,
        assignee_id=assignee.id if assignee else None,
        conversation_state=conversation_state,
        first_response_due_at=first_response_due_at,
        resolution_due_at=resolution_due_at,
        required_action=f"Next action for {ticket_no}",
    )
    db_session.add(row)
    db_session.flush()
    return row


def _handoff(db_session, ticket: Ticket) -> WebchatHandoffRequest:
    conversation = WebchatConversation(
        public_id=f"conv-{_suffix()}",
        visitor_token_hash=f"hash-{_suffix()}",
        tenant_key="today-workbench",
        channel_key="website",
        ticket_id=ticket.id,
        visitor_name="WebChat Visitor",
        status="open",
        handoff_status="requested",
        ai_suspended=True,
    )
    db_session.add(conversation)
    db_session.flush()
    row = WebchatHandoffRequest(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        status="requested",
        reason_code="needs_human",
        recommended_agent_action="Accept and reply",
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_today_workbench_uses_real_ticket_scope_sla_and_handoff_counts(client: TestClient, db_session):
    team_a = _team(db_session, "Team A")
    team_b = _team(db_session, "Team B")
    agent = _user(db_session, "agent", UserRole.agent, team_a)
    other_agent = _user(db_session, "other-agent", UserRole.agent, team_b)
    now = utc_now()

    _ticket(db_session, ticket_no="MINE-SLA", team=team_a, assignee=agent, resolution_due_at=now + timedelta(minutes=15))
    webchat_ticket = _ticket(
        db_session,
        ticket_no="WEBCHAT",
        team=team_a,
        source_channel=SourceChannel.web_chat,
        status=TicketStatus.pending_assignment,
        conversation_state=ConversationState.human_review_required,
    )
    _handoff(db_session, webchat_ticket)
    _ticket(db_session, ticket_no="EMAIL", team=team_a, source_channel=SourceChannel.email)
    _ticket(db_session, ticket_no="WAITING", team=team_a, assignee=agent, status=TicketStatus.waiting_customer)
    _ticket(db_session, ticket_no="OTHER-SLA", team=team_b, assignee=other_agent, resolution_due_at=now + timedelta(minutes=10))
    _ticket(db_session, ticket_no="RESOLVED", team=team_a, status=TicketStatus.resolved, resolution_due_at=now + timedelta(minutes=5))
    _ticket(db_session, ticket_no="URGENT", team=team_a, status=TicketStatus.new, priority=TicketPriority.urgent)

    response = client.get("/api/workbench/today", headers=_headers(agent))

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["user"]["id"] == agent.id
    assert payload["metrics"]["visible_open_tickets"] == 5
    assert payload["metrics"]["my_open_tickets"] == 2
    assert payload["metrics"]["sla_risk_30m"] == 1
    assert payload["metrics"]["webchat_handoff_requested"] == 1
    assert payload["metrics"]["webchat_waiting"] == 1
    assert payload["metrics"]["email_waiting"] == 1
    assert payload["metrics"]["customer_waiting"] == 1
    assert payload["metrics"]["unassigned_visible"] == 3
    assert payload["metrics"]["urgent_open"] == 1
    assert {task["key"] for task in payload["tasks"]} >= {"handoff", "my-tickets", "sla-risk", "customer-waiting", "webchat-waiting", "email-waiting"}
    assert next(task for task in payload["tasks"] if task["key"] == "sla-risk")["source"] == "/api/workbench/today#sla_risk_tickets"
    assert len(payload["sla_risk_tickets"]) == 1
    assert payload["sla_risk_tickets"][0]["ticket_no"].startswith("MINE-SLA")
    assert "/api/auth/me" in payload["source_contracts"]


def test_today_workbench_admin_scope_sees_cross_team_sla_risk(client: TestClient, db_session):
    team_a = _team(db_session, "Admin Team A")
    team_b = _team(db_session, "Admin Team B")
    admin = _user(db_session, "admin", UserRole.admin)
    agent = _user(db_session, "scoped-agent", UserRole.agent, team_a)
    other_agent = _user(db_session, "scoped-other", UserRole.agent, team_b)
    now = utc_now()

    _ticket(db_session, ticket_no="TEAM-A-SLA", team=team_a, assignee=agent, resolution_due_at=now + timedelta(minutes=15))
    _ticket(db_session, ticket_no="TEAM-B-SLA", team=team_b, assignee=other_agent, resolution_due_at=now + timedelta(minutes=25))

    response = client.get("/api/workbench/today", headers=_headers(admin))

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["metrics"]["sla_risk_30m"] == 2
    assert payload["permissions"]["can_assign"] is True
    assert any(task["key"] == "unassigned" for task in payload["tasks"])


def test_today_workbench_requires_effective_ticket_read_capability(client: TestClient, db_session):
    team = _team(db_session, "Denied Team")
    agent = _user(db_session, "denied-agent", UserRole.agent, team)
    db_session.add(UserCapabilityOverride(user_id=agent.id, capability="ticket.read", allowed=False))
    db_session.flush()

    response = client.get("/api/workbench/today", headers=_headers(agent))

    assert response.status_code == 403
    assert response.json()["detail"] == "today_workbench_requires_ticket_read"
