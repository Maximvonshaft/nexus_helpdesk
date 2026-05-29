from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models as _models  # noqa: E402,F401
from app import operator_models as _operator_models  # noqa: E402,F401
from app.auth_service import create_access_token  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.enums import ConversationState, MessageStatus, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import BackgroundJob, Customer, Team, Ticket, TicketOutboundMessage, User  # noqa: E402
from app.operator_models import OperatorTask  # noqa: E402
from app.utils.time import utc_now  # noqa: E402


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


def _team(db_session) -> Team:
    row = Team(name="Today Support", team_type="support")
    db_session.add(row)
    db_session.flush()
    return row


def _user(db_session, *, role: UserRole, team_id: int | None = None) -> User:
    row = User(
        username=f"{role.value}_today",
        display_name=f"{role.value.title()} Today",
        email=f"{role.value}.today@example.test",
        password_hash="x",
        role=role,
        team_id=team_id,
        is_active=True,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _ticket(
    db_session,
    *,
    ticket_no: str,
    team_id: int,
    assignee_id: int | None,
    source_channel: SourceChannel,
    status: TicketStatus,
    minutes_to_due: int | None = None,
    conversation_state: ConversationState = ConversationState.ai_active,
    category: str | None = None,
) -> Ticket:
    customer = Customer(name=f"Customer {ticket_no}", email=f"{ticket_no.lower()}@example.test")
    db_session.add(customer)
    db_session.flush()
    now = utc_now()
    row = Ticket(
        ticket_no=ticket_no,
        title=f"{ticket_no} delivery issue",
        description="delivery issue",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=source_channel,
        priority=TicketPriority.high if minutes_to_due is not None else TicketPriority.medium,
        status=status,
        team_id=team_id,
        assignee_id=assignee_id,
        conversation_state=conversation_state,
        category=category,
        first_response_due_at=now + timedelta(minutes=minutes_to_due) if minutes_to_due is not None else None,
        resolution_due_at=now + timedelta(minutes=minutes_to_due + 30) if minutes_to_due is not None else None,
        customer_request="Where is my parcel?",
    )
    db_session.add(row)
    db_session.flush()
    return row


def _seed_today(db_session, *, role: UserRole):
    team = _team(db_session)
    user = _user(db_session, role=role, team_id=team.id)
    risk_ticket = _ticket(
        db_session,
        ticket_no="TW-001",
        team_id=team.id,
        assignee_id=user.id,
        source_channel=SourceChannel.web_chat,
        status=TicketStatus.in_progress,
        minutes_to_due=20,
        conversation_state=ConversationState.ready_to_reply,
    )
    _ticket(
        db_session,
        ticket_no="TW-002",
        team_id=team.id,
        assignee_id=None,
        source_channel=SourceChannel.email,
        status=TicketStatus.pending_assignment,
        minutes_to_due=None,
        category="email",
    )
    db_session.add(
        OperatorTask(
            source_type="webchat",
            source_id="conversation-1",
            ticket_id=risk_ticket.id,
            webchat_conversation_id=101,
            task_type="handoff",
            status="pending",
            priority=10,
        )
    )
    db_session.add(
        TicketOutboundMessage(
            ticket_id=risk_ticket.id,
            channel=SourceChannel.email,
            status=MessageStatus.dead,
            body="dead email",
            provider_status="dead:smtp_connect_failed",
            max_retries=3,
        )
    )
    db_session.add(
        BackgroundJob(
            queue_name="outbound",
            job_type="probe",
            payload_json="{}",
            status="dead",
            max_attempts=3,
        )
    )
    db_session.flush()
    return user, risk_ticket


def test_today_workbench_agent_contract_uses_real_ticket_and_handoff_counts(tmp_path):
    db_file = tmp_path / "today_agent.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)
    db_session = TestingSession()
    user, risk_ticket = _seed_today(db_session, role=UserRole.agent)
    db_session.commit()

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        response = client.get("/api/lite/today-workbench", headers=_headers(user))
    finally:
        app.dependency_overrides.pop(get_db, None)
        db_session.close()
        Base.metadata.drop_all(engine)

    assert response.status_code == 200, response.text
    payload = response.json()
    tasks = {item["key"]: item for item in payload["tasks"]}
    metrics = {item["key"]: item for item in payload["metrics"]}
    commands = {item["key"]: item for item in payload["command_center"]}

    assert payload["role"] == "agent"
    assert tasks["webchat-handoff"]["count"] == 1
    assert tasks["my-tickets"]["count"] == 1
    assert tasks["sla-risk"]["count"] == 1
    assert tasks["email-waiting"]["count"] == 1
    assert "runtime-recovery" not in tasks
    assert metrics["active_tickets"]["value"] == 2
    assert metrics["sla_risk"]["value"] == 1
    assert payload["sla_priorities"][0]["ticket_id"] == risk_ticket.id
    assert payload["sla_priorities"][0]["minutes_to_due"] <= 20
    assert {item["key"] for item in payload["interaction_states"]} >= {"loading", "empty", "error", "permission", "dirty"}
    assert commands["cmd-webchat"]["enabled"] is True
    assert commands["cmd-ticket"]["enabled"] is True
    assert commands["cmd-email"]["enabled"] is True
    assert commands["cmd-runtime"]["enabled"] is False


def test_today_workbench_admin_includes_runtime_recovery_command(tmp_path):
    db_file = tmp_path / "today_admin.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)
    db_session = TestingSession()
    user, _risk_ticket = _seed_today(db_session, role=UserRole.admin)
    db_session.commit()

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        response = client.get("/api/lite/today-workbench", headers=_headers(user))
    finally:
        app.dependency_overrides.pop(get_db, None)
        db_session.close()
        Base.metadata.drop_all(engine)

    assert response.status_code == 200, response.text
    payload = response.json()
    tasks = {item["key"]: item for item in payload["tasks"]}
    commands = {item["key"]: item for item in payload["command_center"]}

    assert tasks["runtime-recovery"]["count"] == 2
    assert tasks["runtime-recovery"]["enabled"] is True
    assert commands["cmd-runtime"]["enabled"] is True
