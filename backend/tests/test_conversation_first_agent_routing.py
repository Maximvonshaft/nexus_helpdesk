from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException, Request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/nexus_conversation_routing_tests.db",
)
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import (  # noqa: E402,F401
    models,
    models_agent_routing,
    models_control_plane,
    models_operations_dispatch,
    models_osr,
    operator_models,
    tool_models,
    voice_models,
    webchat_models,
)
from app.api.webchat_public import WebchatInitRequest  # noqa: E402
from app.db import Base  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.models import (  # noqa: E402
    Customer,
    Ticket,
    User,
    UserCapabilityOverride,
)
from app.models_agent_routing import (  # noqa: E402
    ConversationControl,
    OperatorAgentState,
)
from app.operator_models import OperatorQueueScopeGrant  # noqa: E402
from app.services.agent_availability_service import availability_summary  # noqa: E402
from app.services.agent_routing_service import (  # noqa: E402
    assign_handoff_to_agent,
    close_conversation,
    request_handoff,
    set_agent_state,
)
from app.services.conversation_first_service import (  # noqa: E402
    create_or_resume_conversation,
)
from app.services.conversation_operator_service import (  # noqa: E402
    read_conversation_thread,
    reply_to_conversation,
)
from app.services.webchat_handoff_service import (  # noqa: E402
    accept_handoff_request,
    decline_handoff_request,
    release_handoff_request,
    resume_ai_for_handoff,
)
from app.webchat_models import WebchatConversation  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "conversation_first_agent_routing.db"
    engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        future=True,
        expire_on_commit=False,
    )
    Base.metadata.create_all(engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/webchat/init",
            "headers": [(b"user-agent", b"pytest")],
            "client": ("127.0.0.1", 12345),
            "scheme": "https",
            "server": ("testserver", 443),
            "query_string": b"",
        }
    )


def _agent(
    db_session,
    *,
    suffix: str = "routing",
    role: UserRole = UserRole.agent,
    granted: bool = True,
) -> User:
    row = User(
        username=f"{suffix}-agent",
        display_name=f"{suffix.title()} Agent",
        password_hash="not-used",
        role=role,
        is_active=True,
    )
    db_session.add(row)
    db_session.flush()
    if granted:
        db_session.add(
            OperatorQueueScopeGrant(
                user_id=row.id,
                tenant_key="default",
                country_code="ME",
                channel_key="webchat",
                enabled=True,
            )
        )
        db_session.flush()
    return row


def _conversation(db_session, *, suffix: str) -> WebchatConversation:
    customer = Customer(
        name=f"Customer {suffix}",
        external_ref=f"customer-{suffix}",
    )
    db_session.add(customer)
    db_session.flush()
    conversation = WebchatConversation(
        public_id=f"conversation-{suffix}",
        visitor_token_hash=f"hash-{suffix}",
        tenant_key="default",
        channel_key="webchat",
        ticket_id=None,
        visitor_name=customer.name,
        status="open",
    )
    db_session.add(conversation)
    db_session.flush()
    db_session.add(
        ConversationControl(
            conversation_id=conversation.id,
            customer_id=customer.id,
            tenant_key="default",
            country_code="ME",
            channel_key="webchat",
        )
    )
    db_session.flush()
    return conversation


def test_public_webchat_initialization_does_not_create_ticket(db_session):
    result = create_or_resume_conversation(
        db_session,
        WebchatInitRequest(
            tenant_key="default",
            channel_key="webchat",
            visitor_name="No Ticket Customer",
            visitor_email="customer@example.com",
        ),
        _request(),
    )

    conversation = db_session.query(WebchatConversation).one()
    control = db_session.query(ConversationControl).one()

    assert result["conversation_id"] == conversation.public_id
    assert conversation.ticket_id is None
    assert db_session.query(Ticket).count() == 0
    assert control.conversation_id == conversation.id
    assert control.customer_id is not None


def test_capacity_one_assigns_fifo_and_close_releases_next_slot(db_session):
    agent = _agent(db_session)
    first = _conversation(db_session, suffix="first")
    second = _conversation(db_session, suffix="second")

    set_agent_state(
        db_session,
        user=agent,
        presence_status="online",
        max_concurrent_conversations=1,
    )

    first_request = request_handoff(
        db_session,
        conversation=first,
        source="ai_runtime",
        trigger_type="runtime_handoff",
        reason_code="needs_human",
    )
    second_request = request_handoff(
        db_session,
        conversation=second,
        source="ai_runtime",
        trigger_type="runtime_handoff",
        reason_code="needs_human",
    )

    db_session.refresh(first_request)
    db_session.refresh(second_request)
    db_session.refresh(first)
    db_session.refresh(second)

    assert first_request.status == "accepted"
    assert first_request.assigned_agent_id == agent.id
    assert first.active_agent_id == agent.id
    assert second_request.status == "requested"
    assert second_request.assigned_agent_id is None
    assert second.active_agent_id is None
    assert db_session.query(Ticket).count() == 0

    full = availability_summary(
        db_session,
        tenant_key="default",
        country_code="ME",
        channel_key="webchat",
        request_row=second_request,
    )
    assert full["online_agents"] == 1
    assert full["total_capacity"] == 1
    assert full["occupied_capacity"] == 1
    assert full["available_capacity"] == 0
    assert full["queue_count"] == 1
    assert full["queue_position"] == 1

    closed = close_conversation(
        db_session,
        conversation=first,
        user=agent,
        outcome="human_resolved",
        note="Resolved during the live conversation.",
    )

    db_session.refresh(second_request)
    db_session.refresh(second)
    state = (
        db_session.query(OperatorAgentState)
        .filter_by(user_id=agent.id)
        .one()
    )

    assert closed["outcome"] == "human_resolved"
    assert first.status == "closed"
    assert second_request.status == "accepted"
    assert second_request.assigned_agent_id == agent.id
    assert second.active_agent_id == agent.id
    assert state.max_concurrent_conversations == 1
    assert db_session.query(Ticket).count() == 0


def test_resume_ai_releases_capacity_and_assigns_next_waiting_conversation(
    db_session,
):
    agent = _agent(db_session, suffix="resume")
    first = _conversation(db_session, suffix="resume-first")
    second = _conversation(db_session, suffix="resume-second")
    set_agent_state(
        db_session,
        user=agent,
        presence_status="online",
        max_concurrent_conversations=1,
    )
    first_request = request_handoff(
        db_session,
        conversation=first,
        source="ai_runtime",
        trigger_type="runtime_handoff",
        reason_code="needs_human",
    )
    second_request = request_handoff(
        db_session,
        conversation=second,
        source="ai_runtime",
        trigger_type="runtime_handoff",
        reason_code="needs_human",
    )

    resumed = resume_ai_for_handoff(
        db_session,
        request_id=first_request.id,
        current_user=agent,
        note="Customer chose to continue with AI.",
    )

    db_session.refresh(first_request)
    db_session.refresh(second_request)
    db_session.refresh(first)
    db_session.refresh(second)
    assert resumed["status"] == "resumed_ai"
    assert first_request.status == "resumed_ai"
    assert first.handoff_status == "none"
    assert first.active_agent_id is None
    assert first.ai_suspended is False
    assert second_request.status == "accepted"
    assert second_request.assigned_agent_id == agent.id
    assert second.active_agent_id == agent.id
    assert db_session.query(Ticket).count() == 0


def test_ticketless_transition_authority_and_reply_capability(db_session):
    agent = _agent(db_session, suffix="transition")
    conversation = _conversation(db_session, suffix="transition")
    set_agent_state(
        db_session,
        user=agent,
        presence_status="online",
        max_concurrent_conversations=1,
    )
    request_row = request_handoff(
        db_session,
        conversation=conversation,
        source="ai_runtime",
        trigger_type="runtime_handoff",
        reason_code="needs_human",
    )

    accepted_thread = read_conversation_thread(
        db_session,
        conversation=conversation,
        user=agent,
    )
    assert accepted_thread["handoff"]["can_release"] is True
    assert accepted_thread["handoff"]["can_resume_ai"] is True
    assert accepted_thread["handoff"]["can_reply"] is True

    released = release_handoff_request(
        db_session,
        request_id=request_row.id,
        current_user=agent,
        note="Return to the shared queue.",
    )
    assert released["status"] == "requested"
    assert released["assigned_agent_id"] is None

    declined = decline_handoff_request(
        db_session,
        request_id=request_row.id,
        current_user=agent,
        reason_code="operator_capacity",
        note="Temporarily unavailable.",
    )
    assert declined["status"] == "requested"
    assert declined["declined_by_me"] is True

    accepted = accept_handoff_request(
        db_session,
        request_id=request_row.id,
        current_user=agent,
        note="Accepted again.",
    )
    assert accepted["status"] == "accepted"
    result = reply_to_conversation(
        db_session,
        conversation=conversation,
        user=agent,
        body="I am handling this conversation now.",
    )
    message = result["message"]
    assert message["direction"] == "agent"
    assert message["body_text"] == "I am handling this conversation now."
    assert message["delivery_status"] == "sent"

    db_session.add(
        UserCapabilityOverride(
            user_id=agent.id,
            capability="outbound.send",
            allowed=False,
        )
    )
    db_session.flush()
    restricted_thread = read_conversation_thread(
        db_session,
        conversation=conversation,
        user=agent,
    )
    assert restricted_thread["handoff"]["can_reply"] is False
    with pytest.raises(HTTPException) as exc:
        reply_to_conversation(
            db_session,
            conversation=conversation,
            user=agent,
            body="This must be denied.",
        )
    assert exc.value.status_code == 403
    assert db_session.query(Ticket).count() == 0


def test_manager_without_explicit_scope_grant_cannot_receive_ticketless_work(
    db_session,
):
    manager = _agent(
        db_session,
        suffix="unscoped-manager",
        role=UserRole.manager,
        granted=False,
    )
    conversation = _conversation(db_session, suffix="manager-scope")
    set_agent_state(
        db_session,
        user=manager,
        presence_status="online",
        max_concurrent_conversations=1,
    )

    request_row = request_handoff(
        db_session,
        conversation=conversation,
        source="ai_runtime",
        trigger_type="runtime_handoff",
        reason_code="needs_human",
    )

    db_session.refresh(request_row)
    assert request_row.status == "requested"
    assert request_row.assigned_agent_id is None
    with pytest.raises(HTTPException) as exc:
        assign_handoff_to_agent(
            db_session,
            request_row=request_row,
            conversation=conversation,
            user=manager,
            mode="manual",
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == "agent_scope_not_authorized"
