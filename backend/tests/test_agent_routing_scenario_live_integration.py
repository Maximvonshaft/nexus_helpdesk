from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/agent-routing-scenario-live.db",
)

from app.db import Base
from app.enums import (
    SourceChannel,
    TicketPriority,
    TicketSource,
    TicketStatus,
    UserRole,
)
from app.model_registry import register_all_models
from app.models import Team, Tenant, Ticket, User
from app.models_agent_routing import ConversationControl, OperatorAgentState
from app.models_handoff_routing import (
    HandoffRoutingCandidateAttempt,
    HandoffRoutingPlan,
)
from app.operator_models import OperatorQueueScopeGrant
from app.services.agent_routing_service import (
    fill_agent_capacity,
    request_handoff,
)
from app.utils.time import utc_now
from app.webchat_models import WebchatConversation

register_all_models()


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'agent-routing-scenario-live.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    Base.metadata.create_all(engine)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _agent(
    db_session,
    *,
    tenant: Tenant,
    team: Team,
    username: str,
    online: bool,
) -> tuple[User, OperatorAgentState]:
    user = User(
        tenant_id=tenant.id,
        tenant_assignment_source="test",
        tenant_assignment_version="scenario-live",
        username=username,
        display_name=username,
        email=f"{username}@example.test",
        password_hash="x",
        role=UserRole.manager,
        team_id=team.id,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    state = OperatorAgentState(
        user_id=user.id,
        status="online" if online else "offline",
        max_concurrent_conversations=3,
        voice_enabled=True,
        max_concurrent_voice_calls=1,
        voice_wrap_up_seconds=30,
        last_heartbeat_at=utc_now() if online else None,
        status_changed_at=utc_now(),
    )
    db_session.add(state)
    db_session.flush()
    return user, state


def _fixture(db_session, *, correct_online: bool = True):
    tenant = Tenant(
        tenant_key="scenario-live",
        display_name="Scenario Live",
        is_active=True,
    )
    db_session.add(tenant)
    db_session.flush()
    team = Team(
        tenant_id=tenant.id,
        tenant_assignment_source="test",
        tenant_assignment_version="scenario-live",
        name="Delivery Exceptions",
        team_type="support",
        is_active=True,
    )
    db_session.add(team)
    db_session.flush()
    correct, correct_state = _agent(
        db_session,
        tenant=tenant,
        team=team,
        username="scenario-correct",
        online=correct_online,
    )
    wrong, _wrong_state = _agent(
        db_session,
        tenant=tenant,
        team=team,
        username="scenario-wrong-queue",
        online=True,
    )
    db_session.add_all(
        [
            OperatorQueueScopeGrant(
                user_id=correct.id,
                tenant_key=tenant.tenant_key,
                country_code="CH",
                channel_key="website",
                queue_key="delivery_exceptions",
                enabled=True,
                granted_by=correct.id,
            ),
            OperatorQueueScopeGrant(
                user_id=wrong.id,
                tenant_key=tenant.tenant_key,
                country_code="CH",
                channel_key="website",
                queue_key="customer_support",
                enabled=True,
                granted_by=correct.id,
            ),
        ]
    )
    ticket = Ticket(
        tenant_id=tenant.id,
        tenant_assignment_source="test",
        tenant_assignment_version="scenario-live",
        ticket_no="SCENARIO-LIVE-1",
        title="Repeated failed delivery",
        description="Live scenario routing integration",
        source=TicketSource.manual,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.high,
        status=TicketStatus.new,
        case_type="failed_attempt",
        country_code="CH",
        team_id=team.id,
        created_by=correct.id,
    )
    db_session.add(ticket)
    db_session.flush()
    conversation = WebchatConversation(
        public_id="scenario-live-conversation",
        visitor_token_hash="hash",
        tenant_key=tenant.tenant_key,
        channel_key="website",
        ticket_id=ticket.id,
        status="open",
    )
    db_session.add(conversation)
    db_session.flush()
    db_session.add(
        ConversationControl(
            conversation_id=conversation.id,
            tenant_key=tenant.tenant_key,
            country_code="CH",
            channel_key="website",
        )
    )
    db_session.flush()
    return conversation, correct, correct_state, wrong


def test_live_handoff_creation_routes_only_through_scenario_queue(db_session):
    conversation, correct, _correct_state, wrong = _fixture(db_session)

    request = request_handoff(
        db_session,
        conversation=conversation,
        source="ai_auto",
        trigger_type="scenario_live_test",
        reason_code="delivery_failure",
    )
    plan = (
        db_session.query(HandoffRoutingPlan)
        .filter(HandoffRoutingPlan.request_id == request.id)
        .one()
    )

    assert plan.owner_queue_key == "delivery_exceptions"
    assert request.status == "accepted"
    assert request.assigned_agent_id == correct.id
    assert request.assigned_agent_id != wrong.id
    assert plan.status == "assigned"
    assert plan.assigned_agent_id == correct.id
    attempts = (
        db_session.query(HandoffRoutingCandidateAttempt)
        .filter(HandoffRoutingCandidateAttempt.plan_id == plan.id)
        .all()
    )
    assert [(row.agent_id, row.outcome) for row in attempts] == [
        (correct.id, "accepted")
    ]


def test_due_generation_reenters_live_capacity_routing(db_session):
    conversation, correct, correct_state, _wrong = _fixture(
        db_session,
        correct_online=False,
    )
    request = request_handoff(
        db_session,
        conversation=conversation,
        source="ai_auto",
        trigger_type="scenario_retry_test",
        reason_code="delivery_failure",
    )
    plan = (
        db_session.query(HandoffRoutingPlan)
        .filter(HandoffRoutingPlan.request_id == request.id)
        .one()
    )
    assert request.status == "requested"
    assert plan.status == "retry_scheduled"

    correct_state.status = "online"
    correct_state.last_heartbeat_at = utc_now()
    correct_state.updated_at = utc_now()
    plan.next_retry_at = utc_now()
    db_session.flush()

    assigned = fill_agent_capacity(db_session, user=correct)

    assert len(assigned) == 1
    assert request.status == "accepted"
    assert request.assigned_agent_id == correct.id
    assert plan.current_generation == 2
    assert plan.status == "assigned"


def test_private_primitives_have_no_parallel_repository_import_path():
    app_root = Path(__file__).resolve().parents[1] / "app"
    references = []
    for path in app_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "agent_routing_primitives" in text:
            references.append(path.relative_to(app_root).as_posix())
    assert references == ["services/agent_routing_service.py"]
