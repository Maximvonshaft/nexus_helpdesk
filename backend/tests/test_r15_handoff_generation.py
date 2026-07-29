from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/r15-handoff-generation.db")

from app.db import Base
from app.enums import SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole
from app.model_registry import register_all_models
from app.models import Team, Tenant, Ticket, User
from app.models_agent_routing import ConversationControl, OperatorAgentState
from app.operator_models import OperatorQueueScopeGrant
from app.services import agent_routing_service
from app.services.canonical_handoff_acceptance import accept_handoff_request as _install_convergence
from app.services.handoff_routing_authority import (
    activate_due_generation,
    ensure_handoff_routing_plan,
    record_candidate_attempt,
    schedule_retry_or_exhaust,
)
from app.utils.time import utc_now
from app.webchat_models import WebchatConversation, WebchatHandoffDecision, WebchatHandoffRequest

register_all_models()
del _install_convergence


def test_historical_decline_is_reconsidered_in_next_generation(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'handoff.db'}", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as db:
        tenant = Tenant(tenant_key="r15-generation", display_name="R15 Generation", is_active=True)
        db.add(tenant)
        db.flush()
        team = Team(
            tenant_id=tenant.id,
            tenant_assignment_source="test",
            tenant_assignment_version="r15",
            name="R15 Generation Team",
            is_active=True,
        )
        db.add(team)
        db.flush()
        user = User(
            tenant_id=tenant.id,
            tenant_assignment_source="test",
            tenant_assignment_version="r15",
            username="r15-generation-user",
            display_name="R15 Generation User",
            email="r15-generation@example.test",
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
                    voice_enabled=True,
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
                    queue_key="delivery_exceptions",
                    enabled=True,
                    granted_by=user.id,
                ),
            ]
        )
        ticket = Ticket(
            tenant_id=tenant.id,
            tenant_assignment_source="test",
            tenant_assignment_version="r15",
            ticket_no="R15-GENERATION-001",
            title="Repeated delivery failure",
            description="Generation contract",
            source=TicketSource.manual,
            source_channel=SourceChannel.web_chat,
            priority=TicketPriority.high,
            status=TicketStatus.pending_assignment,
            case_type="failed_attempt",
            country_code="CH",
            team_id=team.id,
            created_by=user.id,
        )
        db.add(ticket)
        db.flush()
        conversation = WebchatConversation(
            public_id="r15-generation-conversation",
            visitor_token_hash="hash",
            tenant_key=tenant.tenant_key,
            channel_key="website",
            ticket_id=ticket.id,
            status="open",
        )
        db.add(conversation)
        db.flush()
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
            trigger_type="generation_test",
            status="requested",
            reason_code="delivery_failure",
        )
        db.add_all([control, request])
        db.flush()
        plan = ensure_handoff_routing_plan(db, request_row=request)
        assert plan is not None
        db.add(
            WebchatHandoffDecision(
                request_id=request.id,
                actor_id=user.id,
                decision="declined",
                reason_code="agent_declined",
                created_at=utc_now(),
            )
        )
        for channel_kind in ("text", "voice"):
            record_candidate_attempt(
                db,
                plan=plan,
                request_id=request.id,
                agent_id=user.id,
                channel_kind=channel_kind,
                outcome="declined" if channel_kind == "text" else "expired",
                reason_code="generation_one_complete",
            )
        db.flush()

        assert agent_routing_service._eligible_text_request_for_agent(db, user=user) is None
        assert agent_routing_service._core._agent_has_prior_voice_offer(
            db,
            handoff_request_id=request.id,
            agent_id=user.id,
        ) is True

        assert schedule_retry_or_exhaust(db, plan=plan, reason_code="generation_exhausted") == "retry_scheduled"
        plan.next_retry_at = utc_now() - timedelta(seconds=1)
        db.flush()
        assert activate_due_generation(db, plan=plan) is True

        candidate = agent_routing_service._eligible_text_request_for_agent(db, user=user)
        assert candidate is not None and candidate[0].id == request.id
        assert agent_routing_service._core._agent_has_prior_voice_offer(
            db,
            handoff_request_id=request.id,
            agent_id=user.id,
        ) is False

    Base.metadata.drop_all(engine)
    engine.dispose()
