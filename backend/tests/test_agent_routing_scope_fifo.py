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
    "sqlite:////tmp/nexus_agent_routing_scope_fifo.db",
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
from app.db import Base  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.models import Customer, User  # noqa: E402
from app.models_agent_routing import ConversationControl  # noqa: E402
from app.operator_models import OperatorQueueScopeGrant  # noqa: E402
from app.services.agent_routing_service import (  # noqa: E402
    request_handoff,
    set_agent_state,
)
from app.services.webchat_handoff_service import (  # noqa: E402
    list_handoff_queue,
    resume_ai_for_handoff,
)
from app.webchat_models import WebchatConversation  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'agent_routing_scope_fifo.db'}",
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


def _agent(db_session, *, suffix: str, country_code: str = "ME") -> User:
    user = User(
        username=f"{suffix}-agent",
        display_name=f"{suffix.title()} Agent",
        password_hash="not-used",
        role=UserRole.agent,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(
        OperatorQueueScopeGrant(
            user_id=user.id,
            tenant_key="default",
            country_code=country_code,
            channel_key="webchat",
            enabled=True,
        )
    )
    db_session.flush()
    return user


def _conversation(
    db_session,
    *,
    suffix: str,
    country_code: str,
) -> WebchatConversation:
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
            country_code=country_code,
            channel_key="webchat",
        )
    )
    db_session.flush()
    return conversation


def _handoff(db_session, *, conversation: WebchatConversation):
    return request_handoff(
        db_session,
        conversation=conversation,
        source="ai_runtime",
        trigger_type="runtime_handoff",
        reason_code="needs_human",
    )


def test_scope_fifo_finds_eligible_request_after_large_foreign_backlog(db_session):
    agent = _agent(db_session, suffix="fifo")
    set_agent_state(
        db_session,
        user=agent,
        presence_status="online",
        max_concurrent_conversations=1,
    )

    for index in range(105):
        foreign = _conversation(
            db_session,
            suffix=f"foreign-{index:03d}",
            country_code="AL",
        )
        request = _handoff(db_session, conversation=foreign)
        assert request.status == "requested"
        assert request.assigned_agent_id is None

    eligible = _conversation(
        db_session,
        suffix="eligible-after-foreign-backlog",
        country_code="ME",
    )
    eligible_request = _handoff(db_session, conversation=eligible)

    db_session.refresh(eligible_request)
    db_session.refresh(eligible)
    assert eligible_request.status == "accepted"
    assert eligible_request.assigned_agent_id == agent.id
    assert eligible.active_agent_id == agent.id


def test_ticketless_queue_filters_scope_before_page_limit(db_session):
    agent = _agent(db_session, suffix="queue")

    for index in range(6):
        foreign = _conversation(
            db_session,
            suffix=f"queue-foreign-{index:02d}",
            country_code="AL",
        )
        _handoff(db_session, conversation=foreign)

    visible_ids: list[int] = []
    for index in range(2):
        visible = _conversation(
            db_session,
            suffix=f"queue-visible-{index:02d}",
            country_code="ME",
        )
        visible_ids.append(_handoff(db_session, conversation=visible).id)

    result = list_handoff_queue(
        db_session,
        agent,
        view="requested",
        include_declined=False,
        limit=2,
    )

    assert [item["id"] for item in result["items"]] == visible_ids


def test_assigned_agent_can_resume_ai_but_other_agent_cannot(db_session):
    owner = _agent(db_session, suffix="resume-owner")
    other = _agent(db_session, suffix="resume-other")
    set_agent_state(
        db_session,
        user=owner,
        presence_status="online",
        max_concurrent_conversations=1,
    )
    conversation = _conversation(
        db_session,
        suffix="resume-owned",
        country_code="ME",
    )
    request = _handoff(db_session, conversation=conversation)
    db_session.refresh(request)
    assert request.status == "accepted"
    assert request.assigned_agent_id == owner.id

    with pytest.raises(HTTPException) as exc:
        resume_ai_for_handoff(
            db_session,
            request_id=request.id,
            current_user=other,
            note="Must not resume another agent's accepted conversation.",
        )
    assert exc.value.status_code == 403

    result = resume_ai_for_handoff(
        db_session,
        request_id=request.id,
        current_user=owner,
        note="Customer chose to continue with AI.",
    )
    assert result["status"] == "resumed_ai"
    assert result["can_resume_ai"] is False
