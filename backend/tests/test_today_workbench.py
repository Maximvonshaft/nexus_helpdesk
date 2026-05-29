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
from app.enums import ConversationState, JobStatus, MessageStatus, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AdminAuditLog, BackgroundJob, ChannelAccount, IntegrationRequestLog, OutboundEmailAccount, Team, Ticket, TicketOutboundMessage, User  # noqa: E402
from app.utils.time import utc_now  # noqa: E402
from app.webchat_models import WebchatConversation, WebchatHandoffRequest  # noqa: E402


@pytest.fixture()
def db_session():
    db_file = ROOT / f".today_workbench_contract_{_uid()}.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False}, future=True)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
        db_file.unlink(missing_ok=True)


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


def _team(db_session, name: str = "Support") -> Team:
    row = Team(name=f"{name} {_uid()}", team_type="support")
    db_session.add(row)
    db_session.flush()
    return row


def _user(db_session, role: UserRole, *, team_id: int | None = None) -> User:
    row = User(
        username=f"today-{role.value}-{_uid()}",
        display_name=f"Today {role.value}",
        email=f"today-{role.value}-{_uid()}@example.test",
        password_hash="test",
        role=role,
        team_id=team_id,
        is_active=True,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _ticket(db_session, *, assignee_id: int | None, team_id: int | None, status: TicketStatus = TicketStatus.in_progress, priority: TicketPriority = TicketPriority.high) -> Ticket:
    row = Ticket(
        ticket_no=f"TODAY-{_uid()}",
        title="Today workbench contract",
        description="Customer needs help.",
        source=TicketSource.user_message,
        source_channel=SourceChannel.web_chat,
        priority=priority,
        status=status,
        assignee_id=assignee_id,
        team_id=team_id,
        conversation_state=ConversationState.human_review_required,
        resolution_due_at=utc_now() + timedelta(hours=1),
    )
    db_session.add(row)
    db_session.flush()
    return row


def _handoff(db_session, ticket: Ticket) -> None:
    conversation = WebchatConversation(
        public_id=f"conv-{_uid()}",
        visitor_token_hash="hash",
        tenant_key="today-contract",
        channel_key="website",
        ticket_id=ticket.id,
        visitor_name="Contract Visitor",
    )
    db_session.add(conversation)
    db_session.flush()
    db_session.add(
        WebchatHandoffRequest(
            conversation_id=conversation.id,
            ticket_id=ticket.id,
            status="requested",
            requested_at=utc_now() - timedelta(minutes=4),
            reason_code="handoff_required",
        )
    )
    db_session.flush()


def test_agent_today_workbench_uses_real_ticket_and_handoff_contracts(client: TestClient, db_session):
    team = _team(db_session)
    agent = _user(db_session, UserRole.agent, team_id=team.id)
    other_team = _team(db_session, "Other")
    visible_ticket = _ticket(db_session, assignee_id=agent.id, team_id=team.id)
    _ticket(db_session, assignee_id=None, team_id=other_team.id)
    _handoff(db_session, visible_ticket)

    response = client.get("/api/today/workbench", headers=_headers(agent))

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["role"] == "agent"
    assert payload["role_label"] == "一线客服"
    assert "/api/today/workbench" in payload["source_contracts"]
    tasks = {item["key"]: item for item in payload["tasks"]}
    assert set(tasks) == {"handoff", "my_tickets", "sla_risk", "customer_waiting"}
    assert tasks["handoff"]["count"] == 1
    assert tasks["my_tickets"]["count"] == 1
    assert tasks["sla_risk"]["count"] == 1
    assert tasks["customer_waiting"]["count"] == 1
    assert {item["state"] for item in payload["interaction_states"]} >= {"loading", "empty", "error", "permission denied", "unsaved changes"}
    assert {item["key"] for item in payload["command_center"]} >= {"cmd-webchat", "cmd-ticket", "cmd-email", "cmd-trace", "cmd-rbac"}


def test_admin_today_workbench_surfaces_runtime_rbac_and_provider_risks(client: TestClient, db_session):
    admin = _user(db_session, UserRole.admin)
    db_session.add(ChannelAccount(provider="whatsapp", account_id=f"wa-{_uid()}", is_active=True, health_status="offline"))
    db_session.add(
        OutboundEmailAccount(
            display_name="Risky SMTP",
            host="smtp.example.test",
            port=587,
            username=f"smtp-{_uid()}",
            password_encrypted="encrypted",
            from_address=f"support-{_uid()}@example.test",
            is_active=True,
            health_status="failed",
            last_test_status="failed",
        )
    )
    ticket = _ticket(db_session, assignee_id=None, team_id=None)
    db_session.add(BackgroundJob(queue_name="default", job_type="sync", payload_json="{}", status=JobStatus.dead))
    db_session.add(TicketOutboundMessage(ticket_id=ticket.id, channel=SourceChannel.email, status=MessageStatus.dead, body="dead"))
    db_session.add(AdminAuditLog(actor_id=admin.id, action="user.capability.update", target_type="user", target_id=admin.id))
    db_session.add(IntegrationRequestLog(endpoint="/api/v1/integration/tasks", method="POST", status_code=500, error_code="upstream_failed"))
    db_session.commit()

    response = client.get("/api/today/workbench", headers=_headers(admin))

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["role"] == "admin"
    tasks = {item["key"]: item for item in payload["tasks"]}
    assert tasks["channel_risk"]["count"] == 2
    assert tasks["dead_jobs"]["count"] == 2
    assert tasks["rbac_review"]["count"] == 1
    assert tasks["integration_errors"]["count"] == 1
    entrypoints = {item["route"] for item in payload["visible_entrypoints"]}
    assert {"/runtime", "/accounts", "/users", "/webcall", "/email"}.issubset(entrypoints)
