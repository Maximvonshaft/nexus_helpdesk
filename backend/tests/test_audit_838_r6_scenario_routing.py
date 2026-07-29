from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/audit-838-r6-routing.db",
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
from app.services.handoff_routing_authority import (
    activate_due_generation,
    eligible_agents,
    ensure_handoff_routing_plan,
    record_candidate_attempt,
    routing_projection,
    schedule_retry_or_exhaust,
)
from app.utils.time import utc_now
from app.webchat_models import WebchatConversation, WebchatHandoffRequest

register_all_models()


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'audit-838-r6-routing.db'}",
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
    role: UserRole = UserRole.manager,
) -> User:
    row = User(
        tenant_id=tenant.id,
        tenant_assignment_source="test",
        tenant_assignment_version="r6",
        username=username,
        display_name=username,
        email=f"{username}@example.test",
        password_hash="x",
        role=role,
        team_id=team.id,
        is_active=True,
    )
    db_session.add(row)
    db_session.flush()
    db_session.add(
        OperatorAgentState(
            user_id=row.id,
            status="online",
            max_concurrent_conversations=3,
            voice_enabled=True,
            max_concurrent_voice_calls=1,
            voice_wrap_up_seconds=30,
            last_heartbeat_at=utc_now(),
            status_changed_at=utc_now(),
        )
    )
    db_session.flush()
    return row


def _routing_fixture(db_session):
    tenant = Tenant(
        tenant_key="audit-r6",
        display_name="Audit R6",
        is_active=True,
    )
    db_session.add(tenant)
    db_session.flush()
    team = Team(
        tenant_id=tenant.id,
        tenant_assignment_source="test",
        tenant_assignment_version="r6",
        name="Audit R6 Delivery Exceptions",
        team_type="support",
        is_active=True,
    )
    db_session.add(team)
    db_session.flush()
    correct = _agent(
        db_session,
        tenant=tenant,
        team=team,
        username="routing-correct",
    )
    wrong_queue = _agent(
        db_session,
        tenant=tenant,
        team=team,
        username="routing-wrong-queue",
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
                user_id=wrong_queue.id,
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
        tenant_assignment_version="r6",
        ticket_no="R6-ROUTING-001",
        title="Repeated failed delivery",
        description="Scenario routing proof",
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
        public_id="r6-routing-conversation",
        visitor_token_hash="hash",
        tenant_key=tenant.tenant_key,
        channel_key="website",
        ticket_id=ticket.id,
        status="open",
    )
    db_session.add(conversation)
    db_session.flush()
    control = ConversationControl(
        conversation_id=conversation.id,
        tenant_key=tenant.tenant_key,
        country_code="CH",
        channel_key="website",
    )
    request = WebchatHandoffRequest(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        source="ai_auto",
        trigger_type="scenario_routing_test",
        status="requested",
        reason_code="delivery_failure",
    )
    db_session.add_all([control, request])
    db_session.flush()
    plan = ensure_handoff_routing_plan(db_session, request_row=request)
    assert plan is not None
    return request, control, plan, correct, wrong_queue


def test_scenario_plan_filters_exact_queue_and_capability(db_session):
    request, control, plan, correct, wrong_queue = _routing_fixture(db_session)

    assert plan.scenario_key == "failed_repeated_delivery_attempt"
    assert plan.owner_queue_key == "delivery_exceptions"
    assert plan.risk_level == "high"
    assert plan.current_generation == 1
    assert plan.max_generations == 3

    candidates = eligible_agents(
        db_session,
        plan=plan,
        control=control,
        channel_kind="text",
    )
    assert [row.id for row, _state in candidates] == [correct.id]
    assert wrong_queue.id not in {row.id for row, _state in candidates}

    projection = routing_projection(db_session, request_id=request.id)
    assert projection is not None
    assert projection["owner_queue_key"] == "delivery_exceptions"
    assert {"ticket.read", "ticket.assign"}.issubset(
        projection["required_capabilities"]
    )


def test_candidate_attempts_are_generation_scoped_with_bounded_exhaustion(db_session):
    request, control, plan, correct, _wrong_queue = _routing_fixture(db_session)

    record_candidate_attempt(
        db_session,
        plan=plan,
        request_id=request.id,
        agent_id=correct.id,
        channel_kind="text",
        outcome="declined",
        reason_code="agent_declined",
    )
    assert eligible_agents(
        db_session,
        plan=plan,
        control=control,
        channel_kind="text",
    ) == []

    assert schedule_retry_or_exhaust(
        db_session,
        plan=plan,
        reason_code="generation_exhausted",
    ) == "retry_scheduled"
    plan.next_retry_at = utc_now() - timedelta(seconds=1)
    db_session.flush()
    assert activate_due_generation(db_session, plan=plan) is True
    assert plan.current_generation == 2
    assert [
        row.id
        for row, _state in eligible_agents(
            db_session,
            plan=plan,
            control=control,
            channel_kind="text",
        )
    ] == [correct.id]

    plan.current_generation = plan.max_generations
    plan.status = "active"
    db_session.flush()
    assert schedule_retry_or_exhaust(
        db_session,
        plan=plan,
        reason_code="no_eligible_candidate",
    ) == "exhausted"
    assert plan.outcome_code == "candidate_exhausted"
    assert plan.exhausted_at is not None
    assert (
        db_session.query(HandoffRoutingCandidateAttempt)
        .filter(HandoffRoutingCandidateAttempt.plan_id == plan.id)
        .count()
        == 1
    )


def test_routing_plan_contract_is_immutable(db_session):
    _request, _control, plan, _correct, _wrong_queue = _routing_fixture(db_session)

    plan.owner_queue_key = "claims_review"
    with pytest.raises(ValueError, match="handoff_routing_plan_immutable"):
        db_session.flush()

    db_session.rollback()
    assert db_session.query(HandoffRoutingPlan).count() == 0
