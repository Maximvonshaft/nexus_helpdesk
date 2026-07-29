from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/r15-manual-handoff.db")

from app.db import Base
from app.enums import SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole
from app.model_registry import register_all_models
from app.models import Team, Tenant, Ticket, User
from app.models_agent_routing import ConversationControl, OperatorAgentState
from app.operator_models import OperatorQueueScopeGrant, OperatorTask
from app.services.canonical_handoff_acceptance import accept_handoff_request
from app.services.handoff_routing_authority import ensure_handoff_routing_plan
from app.services.operator_queue import project_webchat_handoff_tasks
from app.utils.time import utc_now
from app.webchat_models import WebchatConversation, WebchatHandoffRequest

register_all_models()


def _agent(db, *, tenant, team, username, queue_key):  # noqa: ANN001
    user = User(
        tenant_id=tenant.id,
        tenant_assignment_source="test",
        tenant_assignment_version="r15",
        username=username,
        display_name=username,
        email=f"{username}@example.test",
        password_hash="x",
        role=UserRole.manager,
        team_id=team.id,
        is_active=True,
    )
    db.add(user)
    db.flush()
    db.add_all(
        [
            OperatorAgentState(
                user_id=user.id,
                status="online",
                max_concurrent_conversations=3,
                voice_enabled=False,
                max_concurrent_voice_calls=1,
                voice_wrap_up_seconds=30,
                last_heartbeat_at=utc_now(),
                status_changed_at=utc_now(),
            ),
            OperatorQueueScopeGrant(
                user_id=user.id,
                tenant_key=tenant.tenant_key,
                country_code="CH",
                channel_key="website",
                queue_key=queue_key,
                enabled=True,
                granted_by=user.id,
            ),
        ]
    )
    db.flush()
    return user


def _seed(db, suffix: str):  # noqa: ANN001
    tenant = Tenant(
        tenant_key=f"r15-manual-{suffix}",
        display_name=f"R15 Manual {suffix}",
        is_active=True,
    )
    db.add(tenant)
    db.flush()
    team = Team(
        tenant_id=tenant.id,
        tenant_assignment_source="test",
        tenant_assignment_version="r15",
        name=f"R15 Manual Team {suffix}",
        is_active=True,
    )
    db.add(team)
    db.flush()
    eligible = _agent(
        db,
        tenant=tenant,
        team=team,
        username=f"eligible-{suffix}",
        queue_key="delivery_exceptions",
    )
    other = _agent(
        db,
        tenant=tenant,
        team=team,
        username=f"other-{suffix}",
        queue_key="customer_support",
    )
    ticket = Ticket(
        tenant_id=tenant.id,
        tenant_assignment_source="test",
        tenant_assignment_version="r15",
        ticket_no=f"R15-MANUAL-{suffix}",
        title="Repeated delivery failure",
        description="Manual acceptance contract",
        source=TicketSource.manual,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.high,
        status=TicketStatus.pending_assignment,
        case_type="failed_attempt",
        country_code="CH",
        team_id=team.id,
        created_by=eligible.id,
    )
    db.add(ticket)
    db.flush()
    conversation = WebchatConversation(
        public_id=f"r15-manual-conversation-{suffix}",
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
        trigger_type="manual_acceptance_test",
        status="requested",
        reason_code="delivery_failure",
    )
    db.add(request)
    db.flush()
    plan = ensure_handoff_routing_plan(db, request_row=request)
    assert plan is not None
    project_webchat_handoff_tasks(db, tenant_id=tenant.id)
    db.flush()
    return tenant, request, plan, eligible, other


def test_manual_accept_rejects_other_queue_and_updates_projection(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'manual.db'}", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as db:
        _tenant, request, _plan, _eligible, other = _seed(db, "reject")
        with pytest.raises(HTTPException) as exc:
            accept_handoff_request(
                db,
                request_id=request.id,
                current_user=other,
                note="Queue contract check",
            )
        assert exc.value.status_code == 403
        assert exc.value.detail == "agent_scenario_scope_not_authorized"
        db.rollback()

        tenant, request, plan, eligible, _other = _seed(db, "accept")
        result = accept_handoff_request(
            db,
            request_id=request.id,
            current_user=eligible,
            note="Accepted by authorized operator",
        )
        db.flush()
        assert result["status"] == "accepted"
        assert request.assigned_agent_id == eligible.id
        assert plan.status == "assigned"
        task = (
            db.query(OperatorTask)
            .filter(
                OperatorTask.tenant_id == tenant.id,
                OperatorTask.task_type == "handoff",
                OperatorTask.webchat_conversation_id == request.conversation_id,
            )
            .one()
        )
        assert task.source_type == "webchat_handoff"
        assert task.source_id == str(request.id)
        assert task.status == "assigned"
        assert task.assignee_id == eligible.id

    Base.metadata.drop_all(engine)
    engine.dispose()
