from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/audit-838-r15-handoff-authority.db",
)

from app.db import Base
from app.enums import (
    ConversationState,
    SourceChannel,
    TicketPriority,
    TicketSource,
    TicketStatus,
    UserRole,
)
from app.model_registry import register_all_models
from app.models import Team, Tenant, Ticket, User
from app.models_agent_routing import ConversationControl, OperatorAgentState
from app.models_handoff_routing import HandoffRoutingCandidateAttempt
from app.operator_models import OperatorQueueScopeGrant, OperatorTask
from app.services import agent_routing_service as routing
from app.services.handoff_assignment_contract import (
    install_handoff_assignment_contract,
)
from app.services.handoff_routing_authority import (
    activate_due_generation,
    ensure_handoff_routing_plan,
    record_candidate_attempt,
    schedule_retry_or_exhaust,
)
from app.services.operator_queue import create_operator_task
from app.services.stale_text_handoff_reconciliation import (
    OFFLINE_HANDOFF_GRACE_SECONDS,
    reconcile_stale_text_handoffs,
)
from app.services.webchat_handoff_service import accept_handoff_request
from app.utils.time import utc_now
from app.webchat_models import (
    WebchatConversation,
    WebchatHandoffDecision,
    WebchatHandoffRequest,
    WebchatMessage,
)

register_all_models()
install_handoff_assignment_contract()


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'r15-handoff.db'}",
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


def _agent(db, *, tenant: Tenant, team: Team, username: str) -> User:
    row = User(
        tenant_id=tenant.id,
        tenant_assignment_source="pytest",
        tenant_assignment_version="r15",
        username=username,
        display_name=username,
        email=f"{username}@example.test",
        password_hash="x",
        role=UserRole.manager,
        team_id=team.id,
        is_active=True,
    )
    db.add(row)
    db.flush()
    db.add(
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
    db.flush()
    return row


def _fixture(db):
    tenant = Tenant(
        tenant_key="r15-handoff",
        display_name="R15 Handoff",
        is_active=True,
    )
    db.add(tenant)
    db.flush()
    team = Team(
        tenant_id=tenant.id,
        tenant_assignment_source="pytest",
        tenant_assignment_version="r15",
        name="R15 Delivery Exceptions",
        team_type="support",
        is_active=True,
    )
    db.add(team)
    db.flush()
    correct = _agent(
        db,
        tenant=tenant,
        team=team,
        username="r15-handoff-correct",
    )
    wrong = _agent(
        db,
        tenant=tenant,
        team=team,
        username="r15-handoff-wrong",
    )
    db.add_all(
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
        tenant_assignment_source="pytest",
        tenant_assignment_version="r15",
        ticket_no="R15-HANDOFF-001",
        title="Repeated delivery attempt",
        description="Scenario-governed manual acceptance",
        source=TicketSource.manual,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.high,
        status=TicketStatus.new,
        case_type="failed_attempt",
        country_code="CH",
        team_id=team.id,
        created_by=correct.id,
    )
    db.add(ticket)
    db.flush()
    conversation = WebchatConversation(
        public_id="r15-handoff-conversation",
        visitor_token_hash="hash",
        tenant_key=tenant.tenant_key,
        channel_key="website",
        ticket_id=ticket.id,
        status="open",
    )
    db.add(conversation)
    db.flush()
    db.add(
        ConversationControl(
            conversation_id=conversation.id,
            tenant_key=tenant.tenant_key,
            country_code="CH",
            channel_key="website",
        )
    )
    request = WebchatHandoffRequest(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        source="ai_auto",
        trigger_type="scenario_routing_test",
        status="requested",
        reason_code="delivery_failure",
    )
    db.add(request)
    db.flush()
    plan = ensure_handoff_routing_plan(db, request_row=request)
    assert plan is not None
    task, _created = create_operator_task(
        db,
        source_type="webchat",
        source_id=str(conversation.id),
        ticket_id=ticket.id,
        webchat_conversation_id=conversation.id,
        task_type="handoff",
        reason_code=request.reason_code,
    )
    db.flush()
    return request, conversation, plan, task, correct, wrong


def test_manual_accept_revalidates_scenario_queue_and_updates_canonical_projection(
    db_session,
):
    request, _conversation, plan, task, correct, wrong = _fixture(db_session)

    with pytest.raises(HTTPException) as denied:
        accept_handoff_request(
            db_session,
            request_id=request.id,
            current_user=wrong,
        )
    assert denied.value.status_code == 403
    assert denied.value.detail == "agent_scenario_scope_not_authorized"

    result = accept_handoff_request(
        db_session,
        request_id=request.id,
        current_user=correct,
        note="Manual acceptance through canonical routing",
    )
    db_session.refresh(request)
    db_session.refresh(task)
    db_session.refresh(plan)

    assert result["status"] == "accepted"
    assert request.assigned_agent_id == correct.id
    assert plan.status == "assigned"
    assert plan.assigned_agent_id == correct.id
    assert task.source_type == "webchat_handoff"
    assert task.source_id == str(request.id)
    assert task.status == "assigned"
    assert task.assignee_id == correct.id
    assert task.source_version == request.lock_version


def test_prior_decline_does_not_permanently_remove_ticket_candidate_from_next_generation(
    db_session,
):
    request, _conversation, plan, _task, correct, _wrong = _fixture(db_session)
    db_session.add(
        WebchatHandoffDecision(
            request_id=request.id,
            actor_id=correct.id,
            decision="declined",
            reason_code="temporary_capacity",
            created_at=utc_now(),
        )
    )
    record_candidate_attempt(
        db_session,
        plan=plan,
        request_id=request.id,
        agent_id=correct.id,
        channel_kind="text",
        outcome="declined",
        reason_code="temporary_capacity",
    )
    assert schedule_retry_or_exhaust(
        db_session,
        plan=plan,
        reason_code="generation_exhausted",
    ) == "retry_scheduled"
    plan.next_retry_at = utc_now() - timedelta(seconds=1)
    db_session.flush()
    assert activate_due_generation(db_session, plan=plan) is True
    assert plan.current_generation == 2

    candidate = routing._eligible_text_request_for_agent(
        db_session,
        user=correct,
    )
    assert candidate is not None
    assert candidate[0].id == request.id


def test_stale_accepted_text_handoff_releases_capacity_and_remains_routable(
    db_session,
):
    request, conversation, plan, task, correct, _wrong = _fixture(db_session)
    accept_handoff_request(
        db_session,
        request_id=request.id,
        current_user=correct,
        note="Synthetic accepted work that will be abandoned",
    )
    state = (
        db_session.query(OperatorAgentState)
        .filter(OperatorAgentState.user_id == correct.id)
        .one()
    )
    request.accepted_at = utc_now() - timedelta(
        seconds=OFFLINE_HANDOFF_GRACE_SECONDS + 1
    )
    request.updated_at = request.accepted_at
    state.last_heartbeat_at = utc_now() - timedelta(minutes=10)
    db_session.flush()

    result = reconcile_stale_text_handoffs(
        db_session,
        assigned_agent_id=correct.id,
    )
    db_session.flush()
    db_session.refresh(request)
    db_session.refresh(conversation)
    db_session.refresh(plan)
    db_session.refresh(task)
    ticket = db_session.get(Ticket, conversation.ticket_id)
    assert ticket is not None

    assert result["released"] == 1
    assert result["released_request_ids"] == [request.id]
    assert request.status == "requested"
    assert request.assigned_agent_id is None
    assert request.accepted_by_user_id is None
    assert request.accepted_at is None
    assert request.decision_note == "assigned_agent_heartbeat_stale"
    assert conversation.handoff_status == "requested"
    assert conversation.active_agent_id is None
    assert conversation.ai_suspended is True
    assert task.status == "pending"
    assert task.assignee_id is None
    assert ticket.assignee_id is None
    assert ticket.status == TicketStatus.pending_assignment
    assert ticket.conversation_state == ConversationState.human_review_required
    assert ticket.required_action == request.reason_code
    assert plan.status == "active"
    assert plan.current_generation == 2
    assert plan.assigned_agent_id is None
    prior_attempt = (
        db_session.query(HandoffRoutingCandidateAttempt)
        .filter(
            HandoffRoutingCandidateAttempt.plan_id == plan.id,
            HandoffRoutingCandidateAttempt.generation == 1,
            HandoffRoutingCandidateAttempt.agent_id == correct.id,
        )
        .one()
    )
    assert prior_attempt.outcome == "unavailable"
    assert prior_attempt.reason_code == "assigned_agent_heartbeat_stale"

    released_at = request.released_at
    assert released_at is not None
    state.last_heartbeat_at = utc_now()
    db_session.flush()
    candidate = routing._eligible_text_request_for_agent(
        db_session,
        user=correct,
    )
    assert candidate is not None
    assert candidate[0].id == request.id

    accept_handoff_request(
        db_session,
        request_id=request.id,
        current_user=correct,
        note="Accepted in the new routing generation",
    )
    db_session.refresh(request)
    assert request.accepted_at is not None
    assert request.accepted_at >= released_at
    assert request.accepted_by_user_id == correct.id


def test_reconciliation_query_skips_replied_rows_before_scan_limit(db_session):
    request, _conversation, _plan, _task, correct, _wrong = _fixture(db_session)
    state = (
        db_session.query(OperatorAgentState)
        .filter(OperatorAgentState.user_id == correct.id)
        .one()
    )
    now = utc_now()
    state.last_heartbeat_at = now

    for index in range(100):
        accepted_at = now - timedelta(hours=2) + timedelta(seconds=index)
        blocker_conversation = WebchatConversation(
            public_id=f"r15-active-blocker-{index}",
            visitor_token_hash=f"blocker-hash-{index}",
            tenant_key="r15-handoff",
            channel_key="website",
            status="open",
        )
        db_session.add(blocker_conversation)
        db_session.flush()
        blocker_request = WebchatHandoffRequest(
            conversation_id=blocker_conversation.id,
            source="ai_auto",
            trigger_type="active_replied_blocker",
            status="accepted",
            reason_code="active_human_support",
            assigned_agent_id=correct.id,
            accepted_by_user_id=correct.id,
            requested_at=accepted_at,
            accepted_at=accepted_at,
            created_at=accepted_at,
            updated_at=accepted_at,
        )
        db_session.add(blocker_request)
        db_session.flush()
        db_session.add(
            WebchatMessage(
                conversation_id=blocker_conversation.id,
                direction="agent",
                body="The operator has already replied.",
                body_text="The operator has already replied.",
                message_type="text",
                delivery_status="sent",
                author_user_id=correct.id,
                created_at=accepted_at + timedelta(seconds=1),
            )
        )

    accept_handoff_request(
        db_session,
        request_id=request.id,
        current_user=correct,
        note="Target request after the first one hundred active rows",
    )
    request.accepted_at = now - timedelta(hours=1)
    request.updated_at = request.accepted_at
    db_session.flush()

    result = reconcile_stale_text_handoffs(db_session, limit=100)
    db_session.refresh(request)

    assert result["released"] == 1
    assert result["released_request_ids"] == [request.id]
    assert result["inspected"] == 1
    assert request.status == "requested"
