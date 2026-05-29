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
os.environ.setdefault("WEBCHAT_VOICE_ENABLED", "true")
os.environ.setdefault("WEBCHAT_VOICE_PROVIDER", "mock")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models as _models  # noqa: E402,F401
from app import operator_models as _operator_models  # noqa: E402,F401
from app import voice_models as _voice_models  # noqa: E402,F401
from app import webchat_fast_models as _webchat_fast_models  # noqa: E402,F401
from app import webchat_models as _webchat_models  # noqa: E402,F401
from app.auth_service import create_access_token  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.enums import ConversationState, MessageStatus, ResolutionCategory, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Customer, Team, Ticket, TicketOutboundMessage, User, UserCapabilityOverride  # noqa: E402
from app.services.permissions import CAP_TICKET_READ, CAP_WEBCALL_VOICE_QUEUE_VIEW, CAP_WEBCALL_VOICE_READ  # noqa: E402
from app.utils.time import utc_now  # noqa: E402
from app.voice_models import WebchatVoiceSession  # noqa: E402
from app.webchat_models import WebchatConversation, WebchatHandoffRequest  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "workbench_summary.db"
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


@pytest.fixture()
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _uid() -> str:
    return uuid.uuid4().hex[:10]


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def _team(db_session, name: str) -> Team:
    row = Team(name=f"{name}-{_uid()}", team_type="support")
    db_session.add(row)
    db_session.flush()
    return row


def _agent(db_session, team: Team, *, username: str = "workbench-agent") -> User:
    row = User(
        username=f"{username}-{_uid()}",
        display_name="Workbench Agent",
        email=f"{username}-{_uid()}@example.test",
        password_hash="test",
        role=UserRole.agent,
        team_id=team.id,
        is_active=True,
    )
    db_session.add(row)
    db_session.flush()
    for capability in (CAP_WEBCALL_VOICE_QUEUE_VIEW, CAP_WEBCALL_VOICE_READ):
        db_session.add(UserCapabilityOverride(user_id=row.id, capability=capability, allowed=True))
    db_session.flush()
    return row


def _customer(db_session, name: str) -> Customer:
    row = Customer(name=name, email=f"{name.lower().replace(' ', '.')}@example.test", phone="+15550123456")
    db_session.add(row)
    db_session.flush()
    return row


def _ticket(db_session, *, team: Team, customer: Customer, title: str, channel: SourceChannel, status: TicketStatus = TicketStatus.in_progress, priority: TicketPriority = TicketPriority.medium, due_delta: timedelta | None = None, assignee: User | None = None) -> Ticket:
    now = utc_now()
    row = Ticket(
        ticket_no=f"WB-{_uid()}",
        title=title,
        description=title,
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=channel,
        priority=priority,
        status=status,
        resolution_category=ResolutionCategory.none,
        conversation_state=ConversationState.human_owned,
        team_id=team.id,
        assignee_id=assignee.id if assignee else None,
        preferred_reply_channel=channel.value,
        preferred_reply_contact=customer.email,
        first_response_due_at=now + due_delta if due_delta else None,
        resolution_due_at=now + due_delta if due_delta else None,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_workbench_summary_uses_real_ticket_voice_handoff_email_contracts(client: TestClient, db_session):
    team = _team(db_session, "workbench")
    other_team = _team(db_session, "hidden")
    agent = _agent(db_session, team)
    visible_customer = _customer(db_session, "Visible Customer")
    hidden_customer = _customer(db_session, "Hidden Customer")

    email_ticket = _ticket(
        db_session,
        team=team,
        customer=visible_customer,
        title="Email SLA risk",
        channel=SourceChannel.email,
        priority=TicketPriority.high,
        due_delta=timedelta(minutes=15),
        assignee=agent,
    )
    webchat_ticket = _ticket(
        db_session,
        team=team,
        customer=visible_customer,
        title="WebChat handoff waiting",
        channel=SourceChannel.web_chat,
        priority=TicketPriority.urgent,
        due_delta=timedelta(minutes=-5),
    )
    _ticket(
        db_session,
        team=other_team,
        customer=hidden_customer,
        title="Hidden other team ticket",
        channel=SourceChannel.email,
        due_delta=timedelta(minutes=-10),
    )
    conversation = WebchatConversation(
        public_id=f"wb_{_uid()}",
        visitor_token_hash="hash",
        tenant_key="pytest",
        channel_key="website",
        ticket_id=webchat_ticket.id,
        visitor_name="Visible Customer",
        status="open",
        handoff_status="requested",
    )
    db_session.add(conversation)
    db_session.flush()
    handoff = WebchatHandoffRequest(
        conversation_id=conversation.id,
        ticket_id=webchat_ticket.id,
        source="ai_auto",
        trigger_type="handoff_required",
        status="requested",
        reason_code="sla_risk",
        recommended_agent_action="Take over the WebChat before SLA breach.",
        requested_at=utc_now() - timedelta(minutes=6),
    )
    db_session.add(handoff)
    voice = WebchatVoiceSession(
        public_id=f"wv_{_uid()}",
        conversation_id=conversation.id,
        ticket_id=webchat_ticket.id,
        provider="mock",
        provider_room_name=f"webcall_{_uid()}",
        status="ringing",
        ringing_at=utc_now() - timedelta(minutes=2),
        started_at=utc_now() - timedelta(minutes=2),
    )
    db_session.add(voice)
    db_session.add(TicketOutboundMessage(ticket_id=email_ticket.id, channel=SourceChannel.email, status=MessageStatus.draft, subject="Draft", body="Draft body", created_by=agent.id))
    db_session.add(TicketOutboundMessage(ticket_id=email_ticket.id, channel=SourceChannel.email, status=MessageStatus.dead, subject="Dead", body="Dead body", created_by=agent.id))
    db_session.commit()
    db_session.expire_all()

    response = client.get("/api/workbench/summary?limit=20", headers=_headers(agent))

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["user"]["id"] == agent.id
    assert "/api/workbench/summary" in payload["data_sources"]
    metric_keys = {metric["key"]: metric["value"] for metric in payload["metrics"]}
    assert metric_keys["open"] == 2
    assert metric_keys["mine"] == 1
    assert metric_keys["sla"] == 2
    assert metric_keys["handoff"] == 1
    assert metric_keys["voice"] == 1
    task_counts = {task["id"]: task["count"] for task in payload["tasks"]}
    assert task_counts["sla-risk"] == 2
    assert task_counts["webchat-handoff"] == 1
    assert task_counts["webcall-incoming"] == 1
    assert task_counts["draft-replies"] == 1
    assert task_counts["failed-outbound"] == 1
    queue_titles = {item["title"] for item in payload["queue"]}
    assert "WebChat handoff waiting" in queue_titles
    assert "Email SLA risk" in queue_titles
    assert "Hidden other team ticket" not in queue_titles
    assert {item["target_route"] for item in payload["queue"]} >= {"/webchat", "/webcall", "/email"}
    interaction_counts = {item["key"]: item["count"] for item in payload["interaction_states"]}
    assert interaction_counts["webchat"] == 1
    assert interaction_counts["webcall"] == 1
    assert interaction_counts["email"] == 1
    assert interaction_counts["failed-send"] == 1


def test_workbench_summary_requires_ticket_read_capability(client: TestClient, db_session):
    team = _team(db_session, "denied")
    user = _agent(db_session, team, username="denied-workbench")
    db_session.add(UserCapabilityOverride(user_id=user.id, capability=CAP_TICKET_READ, allowed=False))
    db_session.commit()

    response = client.get("/api/workbench/summary", headers=_headers(user))

    assert response.status_code == 403
    assert response.json()["detail"] == "workbench_summary_requires_ticket_read"
