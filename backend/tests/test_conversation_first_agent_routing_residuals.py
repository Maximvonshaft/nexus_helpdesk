from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/nexus_conversation_routing_residual_tests.db",
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
from app.api.support_conversations import (  # noqa: E402
    list_support_conversations,
    support_conversation_metrics,
    support_conversation_state,
)
from app.db import Base  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.models import Customer, Ticket, User  # noqa: E402
from app.models_agent_routing import ConversationControl  # noqa: E402
from app.operator_models import OperatorQueueScopeGrant  # noqa: E402
from app.services.agent_routing_service import (  # noqa: E402
    close_conversation,
    request_handoff,
    set_agent_state,
)
from app.services.webchat_handoff_service import (  # noqa: E402
    decline_handoff_request,
)
from app.webchat_models import WebchatConversation  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "conversation_first_agent_routing_residuals.db"
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


def _agent(
    db_session,
    *,
    suffix: str,
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
        _grant(db_session, user_id=row.id)
    return row


def _grant(db_session, *, user_id: int) -> OperatorQueueScopeGrant:
    row = OperatorQueueScopeGrant(
        user_id=user_id,
        tenant_key="default",
        country_code="ME",
        channel_key="webchat",
        enabled=True,
    )
    db_session.add(row)
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


def test_new_handoff_dispatches_oldest_newly_eligible_waiter(db_session):
    agent = _agent(
        db_session,
        suffix="fifo-newly-eligible",
        granted=False,
    )
    older = _conversation(db_session, suffix="fifo-older")
    newer = _conversation(db_session, suffix="fifo-newer")
    set_agent_state(
        db_session,
        user=agent,
        presence_status="online",
        max_concurrent_conversations=1,
    )

    older_request = request_handoff(
        db_session,
        conversation=older,
        source="ai_runtime",
        trigger_type="runtime_handoff",
        reason_code="older_waiter",
    )
    _grant(db_session, user_id=agent.id)
    newer_request = request_handoff(
        db_session,
        conversation=newer,
        source="ai_runtime",
        trigger_type="runtime_handoff",
        reason_code="newer_waiter",
    )

    db_session.refresh(older_request)
    db_session.refresh(newer_request)
    assert older_request.status == "accepted"
    assert older_request.assigned_agent_id == agent.id
    assert newer_request.status == "requested"
    assert newer_request.assigned_agent_id is None
    assert db_session.query(Ticket).count() == 0


def test_declined_waiter_is_not_immediately_auto_assigned_again(db_session):
    agent = _agent(
        db_session,
        suffix="decline-skip",
        granted=False,
    )
    declined_conversation = _conversation(
        db_session,
        suffix="declined-waiter",
    )
    next_conversation = _conversation(db_session, suffix="next-waiter")
    declined_request = request_handoff(
        db_session,
        conversation=declined_conversation,
        source="ai_runtime",
        trigger_type="runtime_handoff",
        reason_code="decline_me",
    )
    _grant(db_session, user_id=agent.id)
    declined = decline_handoff_request(
        db_session,
        request_id=declined_request.id,
        current_user=agent,
        reason_code="agent_skipped",
        note="Temporarily unavailable for this customer.",
    )
    assert declined["declined_by_me"] is True

    next_request = request_handoff(
        db_session,
        conversation=next_conversation,
        source="ai_runtime",
        trigger_type="runtime_handoff",
        reason_code="next_waiter",
    )
    set_agent_state(
        db_session,
        user=agent,
        presence_status="online",
        max_concurrent_conversations=1,
    )

    db_session.refresh(declined_request)
    db_session.refresh(next_request)
    assert declined_request.status == "requested"
    assert declined_request.assigned_agent_id is None
    assert next_request.status == "accepted"
    assert next_request.assigned_agent_id == agent.id


def test_ticketless_support_views_include_open_closed_metrics_and_state(
    db_session,
):
    operator = _agent(
        db_session,
        suffix="support-projection",
        role=UserRole.admin,
    )
    open_conversation = _conversation(db_session, suffix="support-open")
    closed_conversation = _conversation(db_session, suffix="support-closed")
    waiting_conversation = _conversation(db_session, suffix="support-waiting")

    close_conversation(
        db_session,
        conversation=closed_conversation,
        user=operator,
        outcome="human_resolved",
        note="Closed without creating a Ticket.",
    )
    waiting_request = request_handoff(
        db_session,
        conversation=waiting_conversation,
        source="ai_runtime",
        trigger_type="runtime_handoff",
        reason_code="needs_human",
    )

    open_result = list_support_conversations(
        q=None,
        channel="webchat",
        view="open",
        limit=80,
        db=db_session,
        current_user=operator,
    )
    closed_result = list_support_conversations(
        q=None,
        channel="webchat",
        view="closed",
        limit=80,
        db=db_session,
        current_user=operator,
    )
    metrics = support_conversation_metrics(
        since_hours=24,
        db=db_session,
        current_user=operator,
    )
    state = support_conversation_state(
        db=db_session,
        current_user=operator,
    )

    open_ids = {item["conversation_id"] for item in open_result["items"]}
    closed_ids = {
        item["conversation_id"] for item in closed_result["items"]
    }
    assert open_conversation.public_id in open_ids
    assert waiting_conversation.public_id in open_ids
    assert closed_conversation.public_id in closed_ids
    assert metrics["total"] == 3
    assert metrics["needs_human"] == 1
    assert metrics["by_state"]["closed"] == 1
    assert metrics["by_state"]["human_review_required"] == 1
    assert state["open"] == 2
    assert state["requested_handoffs"] == 1
    assert state["my_handoffs"] == 0
    assert waiting_request.status == "requested"
    assert db_session.query(Ticket).count() == 0


def test_closed_conversation_idempotency_still_requires_scope(db_session):
    scoped_agent = _agent(db_session, suffix="scoped-close")
    unscoped_manager = _agent(
        db_session,
        suffix="unscoped-close",
        role=UserRole.manager,
        granted=False,
    )
    conversation = _conversation(db_session, suffix="closed-scope")
    close_conversation(
        db_session,
        conversation=conversation,
        user=scoped_agent,
        outcome="human_resolved",
    )

    with pytest.raises(HTTPException) as exc:
        close_conversation(
            db_session,
            conversation=conversation,
            user=unscoped_manager,
            outcome="human_resolved",
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == "agent_scope_not_authorized"
