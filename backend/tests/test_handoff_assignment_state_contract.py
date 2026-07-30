from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/handoff-assignment-state-contract.db",
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
from app.services.handoff_assignment_state_contract import (
    install_handoff_assignment_state_contract,
)
from app.utils.time import utc_now
from app.webchat_models import WebchatConversation, WebchatHandoffRequest

register_all_models()
install_handoff_assignment_state_contract()


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'handoff-state.db'}",
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


def _fixture(db_session):
    tenant = Tenant(
        tenant_key="handoff-state",
        display_name="Handoff State",
        is_active=True,
    )
    db_session.add(tenant)
    db_session.flush()
    team = Team(
        tenant_id=tenant.id,
        tenant_assignment_source="pytest",
        tenant_assignment_version="handoff-state-v1",
        name="Handoff State Team",
        team_type="support",
        is_active=True,
    )
    db_session.add(team)
    db_session.flush()
    agent = User(
        tenant_id=tenant.id,
        tenant_assignment_source="pytest",
        tenant_assignment_version="handoff-state-v1",
        username="handoff-state-agent",
        display_name="Handoff State Agent",
        email="handoff-state-agent@example.test",
        password_hash="x",
        role=UserRole.manager,
        team_id=team.id,
        is_active=True,
    )
    db_session.add(agent)
    db_session.flush()
    ticket = Ticket(
        tenant_id=tenant.id,
        tenant_assignment_source="pytest",
        tenant_assignment_version="handoff-state-v1",
        ticket_no="HANDOFF-STATE-001",
        title="Customer requires a human owner",
        description="Accepted Handoff must atomically own the Case",
        source=TicketSource.user_message,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.pending_assignment,
        conversation_state=ConversationState.human_review_required,
        required_action="Accept the waiting Handoff",
        team_id=team.id,
    )
    db_session.add(ticket)
    db_session.flush()
    conversation = WebchatConversation(
        public_id="handoff-state-conversation",
        visitor_token_hash="hash",
        tenant_key=tenant.tenant_key,
        channel_key="website",
        ticket_id=ticket.id,
        status="open",
    )
    db_session.add(conversation)
    db_session.flush()
    request = WebchatHandoffRequest(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        source="customer_action",
        trigger_type="card_action",
        status="requested",
        reason_code="customer_requested_human_support",
    )
    db_session.add(request)
    db_session.flush()
    return agent, ticket, conversation, request


def test_accepted_handoff_projects_canonical_ticket_ownership(db_session):
    agent, ticket, conversation, request = _fixture(db_session)
    now = utc_now()
    request.status = "accepted"
    request.accepted_by_user_id = agent.id
    request.assigned_agent_id = agent.id
    request.accepted_at = now
    conversation.current_handoff_request_id = request.id
    conversation.handoff_status = "accepted"
    conversation.active_agent_id = agent.id
    conversation.ai_suspended = True
    conversation.ai_suspended_at = now
    conversation.ai_suspended_by = agent.id
    conversation.ai_suspended_reason = "handoff_accepted"

    db_session.flush()

    assert ticket.assignee_id == agent.id
    assert ticket.status == TicketStatus.in_progress
    assert ticket.conversation_state == ConversationState.human_owned
    assert ticket.required_action is None
    assert ticket.updated_at is not None


def test_accepted_handoff_fails_closed_without_conversation_ownership(db_session):
    agent, _ticket, _conversation, request = _fixture(db_session)
    request.status = "accepted"
    request.accepted_by_user_id = agent.id
    request.assigned_agent_id = agent.id
    request.accepted_at = utc_now()

    with pytest.raises(
        RuntimeError,
        match="accepted_handoff_conversation_projection_invalid",
    ):
        db_session.flush()


def test_ticketless_accepted_handoff_stays_outside_ticket_projection(db_session):
    agent, _ticket, _conversation, _request = _fixture(db_session)
    conversation = WebchatConversation(
        public_id="ticketless-handoff-state-conversation",
        visitor_token_hash="ticketless-hash",
        tenant_key="handoff-state",
        channel_key="voice",
        status="open",
    )
    db_session.add(conversation)
    db_session.flush()
    request = WebchatHandoffRequest(
        conversation_id=conversation.id,
        source="voice_call",
        trigger_type="voice_inbound",
        status="accepted",
        reason_code="inbound_voice_call",
        accepted_by_user_id=agent.id,
        assigned_agent_id=agent.id,
        requested_at=utc_now(),
        accepted_at=utc_now(),
    )
    db_session.add(request)

    db_session.flush()

    assert request.status == "accepted"
    assert request.ticket_id is None
    assert conversation.ticket_id is None
