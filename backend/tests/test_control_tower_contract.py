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
from app import voice_models as _voice_models  # noqa: E402,F401
from app import webchat_models as _webchat_models  # noqa: E402,F401
from app.auth_service import create_access_token  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.enums import ConversationState, JobStatus, MessageStatus, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    AIConfigResource,
    AdminAuditLog,
    BackgroundJob,
    ChannelAccount,
    Customer,
    MarketBulletin,
    OutboundEmailAccount,
    Team,
    Ticket,
    TicketOutboundMessage,
    User,
    UserCapabilityOverride,
)
from app.operator_models import OperatorTask  # noqa: E402
from app.utils.time import utc_now  # noqa: E402
from app.voice_models import WebchatVoiceSession  # noqa: E402
from app.webchat_models import WebchatConversation  # noqa: E402


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


def _user(db_session, *, role: UserRole, team_id: int | None = None, suffix: str = "") -> User:
    row = User(
        username=f"{role.value}_tower{suffix}",
        display_name=f"{role.value.title()} Tower",
        email=f"{role.value}.tower{suffix}@example.test",
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
    breached: bool = False,
    conversation_state: ConversationState = ConversationState.ai_active,
    category: str | None = None,
) -> Ticket:
    customer = Customer(name=f"Customer {ticket_no}", email=f"{ticket_no.lower()}@example.test", phone="+41790000000")
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
        first_response_breached=breached,
        resolution_breached=breached,
        customer_request="Where is my parcel?",
    )
    db_session.add(row)
    db_session.flush()
    return row


def _seed_control_tower(db_session):
    team = Team(name="Control Tower Support", team_type="support")
    db_session.add(team)
    db_session.flush()
    manager = _user(db_session, role=UserRole.manager, team_id=team.id)
    agent = _user(db_session, role=UserRole.agent, team_id=team.id, suffix="_agent")
    db_session.add(UserCapabilityOverride(user_id=manager.id, capability="runtime.manage", allowed=True))

    risk_ticket = _ticket(
        db_session,
        ticket_no="CT-001",
        team_id=team.id,
        assignee_id=manager.id,
        source_channel=SourceChannel.web_chat,
        status=TicketStatus.in_progress,
        minutes_to_due=20,
        conversation_state=ConversationState.ready_to_reply,
    )
    _ticket(
        db_session,
        ticket_no="CT-002",
        team_id=team.id,
        assignee_id=None,
        source_channel=SourceChannel.email,
        status=TicketStatus.pending_assignment,
        category="email",
    )
    _ticket(
        db_session,
        ticket_no="CT-003",
        team_id=team.id,
        assignee_id=manager.id,
        source_channel=SourceChannel.web_chat,
        status=TicketStatus.waiting_internal,
        minutes_to_due=-10,
        breached=True,
        conversation_state=ConversationState.human_review_required,
    )
    conversation = WebchatConversation(
        public_id="wc_control_tower",
        visitor_token_hash="hash",
        tenant_key="default",
        channel_key="default",
        ticket_id=risk_ticket.id,
        visitor_name="Taylor",
        visitor_email="taylor@example.test",
        status="open",
    )
    db_session.add(conversation)
    db_session.flush()
    db_session.add(
        OperatorTask(
            source_type="webchat",
            source_id="conversation-ct",
            ticket_id=risk_ticket.id,
            webchat_conversation_id=conversation.id,
            task_type="handoff",
            status="pending",
            priority=10,
        )
    )
    db_session.add(
        WebchatVoiceSession(
            public_id="wv_control_tower",
            conversation_id=conversation.id,
            ticket_id=risk_ticket.id,
            provider="mock",
            provider_room_name="mock_room",
            status="ringing",
            mode="visitor_to_agent",
            ringing_at=utc_now(),
        )
    )
    db_session.add(MarketBulletin(title="Severe weather delay", body="Expect delays", summary="Delay", severity="critical", category="delay", is_active=True, starts_at=utc_now() - timedelta(hours=1), ends_at=utc_now() + timedelta(hours=2)))
    db_session.add(
        OutboundEmailAccount(
            display_name="Primary SMTP",
            host="smtp.example.test",
            port=587,
            username="smtp-user",
            password_encrypted="encrypted",
            from_address="support@example.test",
            security_mode="starttls",
            is_active=True,
            health_status="unknown",
            last_test_status="failed",
        )
    )
    db_session.add(ChannelAccount(provider="whatsapp", account_id="wa-control-tower", display_name="WA", is_active=True, health_status="unknown"))
    db_session.add(BackgroundJob(queue_name="runtime", job_type="probe", payload_json="{}", status=JobStatus.dead, max_attempts=3))
    db_session.add(TicketOutboundMessage(ticket_id=risk_ticket.id, channel=SourceChannel.email, status=MessageStatus.dead, body="dead email", provider_status="dead"))
    db_session.add(TicketOutboundMessage(ticket_id=risk_ticket.id, channel=SourceChannel.email, status=MessageStatus.pending, body="pending email", provider_status="queued"))
    db_session.add(AIConfigResource(resource_key="persona.control_tower", config_type="persona", name="Draft persona", is_active=True, published_version=0))
    db_session.add(AdminAuditLog(actor_id=manager.id, action="runtime.requeue", target_type="background_job", target_id=1, created_at=utc_now()))
    db_session.flush()
    return manager, agent


def test_control_tower_manager_contract_uses_real_operational_counts(tmp_path):
    db_file = tmp_path / "control_tower.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)
    db_session = TestingSession()
    manager, _agent = _seed_control_tower(db_session)
    db_session.commit()

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        response = client.get("/api/lite/control-tower", headers=_headers(manager))
    finally:
        app.dependency_overrides.pop(get_db, None)
        db_session.close()
        Base.metadata.drop_all(engine)

    assert response.status_code == 200, response.text
    payload = response.json()
    kpis = {item["key"]: item for item in payload["kpis"]}
    actions = {item["key"]: item for item in payload["manager_actions"]}
    channels = {item["key"]: item for item in payload["channel_health"]}
    lanes = {item["key"]: item for item in payload["governance_lanes"]}
    blocks = {item["key"]: item for item in payload["template_blocks"]}

    assert payload["role"] == "manager"
    assert kpis["active_tickets"]["value"] == 3
    assert kpis["sla_risk"]["value"] == 2
    assert kpis["handoff_waiting"]["value"] == 1
    assert kpis["active_webcalls"]["value"] == 1
    assert kpis["runtime_dead"]["value"] == 2
    assert kpis["active_bulletins"]["value"] == 1
    assert actions["assign-unassigned"]["count"] == 1
    assert actions["assign-unassigned"]["enabled"] is True
    assert actions["recover-runtime"]["count"] == 2
    assert actions["recover-runtime"]["enabled"] is True
    assert actions["fix-email-route"]["enabled"] is False
    assert channels["email"]["risk"] == 2
    assert payload["team_workload"][0]["active_tickets"] == 3
    assert payload["bulletin_impact"][0]["severity"] == "critical"
    assert lanes["rbac-lens"]["value"] == 1
    assert blocks["kpi-tower"]["status"] == "implemented"
    assert payload["facts"]["ready_to_reply"] == 2
    assert payload["facts"]["draft_ai_configs"] == 1


def test_control_tower_requires_management_capability(tmp_path):
    db_file = tmp_path / "control_tower_forbidden.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)
    db_session = TestingSession()
    _manager, agent = _seed_control_tower(db_session)
    db_session.commit()

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        response = client.get("/api/lite/control-tower", headers=_headers(agent))
    finally:
        app.dependency_overrides.pop(get_db, None)
        db_session.close()
        Base.metadata.drop_all(engine)

    assert response.status_code == 403
    assert response.json()["detail"] == "control_tower_requires_management_capability"
