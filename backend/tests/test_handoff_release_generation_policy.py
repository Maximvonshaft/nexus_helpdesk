from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/nexus_handoff_release_generation_policy.db",
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
    fill_agent_capacity,
    request_handoff,
    set_agent_state,
)
from app.services.handoff_routing_policy import (  # noqa: E402
    active_decline_exists,
)
from app.services.webchat_handoff_service import (  # noqa: E402
    release_handoff_request,
)
from app.utils.time import ensure_utc, utc_now  # noqa: E402
from app.webchat_models import (  # noqa: E402
    WebchatConversation,
    WebchatHandoffDecision,
)


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'handoff_release_generation_policy.db'}",
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


def _agent(db_session) -> User:
    user = User(
        username="release-generation-agent",
        display_name="Release Generation Agent",
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
            country_code="ME",
            channel_key="webchat",
            enabled=True,
        )
    )
    db_session.flush()
    set_agent_state(
        db_session,
        user=user,
        presence_status="online",
        max_concurrent_conversations=1,
    )
    return user


def _conversation(db_session) -> WebchatConversation:
    customer = Customer(
        name="Release Generation Customer",
        external_ref="release-generation-customer",
    )
    db_session.add(customer)
    db_session.flush()
    conversation = WebchatConversation(
        public_id="conversation-release-generation",
        visitor_token_hash="release-generation-hash",
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


def test_release_excludes_previous_owner_only_for_the_new_generation(db_session):
    agent = _agent(db_session)
    conversation = _conversation(db_session)
    request_row = request_handoff(
        db_session,
        conversation=conversation,
        source="ai_runtime",
        trigger_type="runtime_handoff",
        reason_code="needs_human",
    )
    db_session.refresh(request_row)
    db_session.refresh(conversation)
    assert request_row.status == "accepted"
    assert request_row.assigned_agent_id == agent.id
    assert conversation.active_agent_id == agent.id
    previous_generation = request_row.routing_generation

    release_handoff_request(
        db_session,
        request_id=request_row.id,
        current_user=agent,
        note="Route this conversation to another eligible operator.",
    )

    db_session.refresh(request_row)
    db_session.refresh(conversation)
    assert request_row.routing_generation == previous_generation + 1
    assert request_row.status == "requested"
    assert request_row.assigned_agent_id is None
    assert conversation.active_agent_id is None
    decision = (
        db_session.query(WebchatHandoffDecision)
        .filter(
            WebchatHandoffDecision.request_id == request_row.id,
            WebchatHandoffDecision.actor_id == agent.id,
            WebchatHandoffDecision.routing_generation
            == request_row.routing_generation,
        )
        .one()
    )
    assert decision.decision == "declined"
    assert decision.reason_code == "agent_released"
    assert decision.expires_at is not None
    assert ensure_utc(decision.expires_at) > ensure_utc(utc_now())
    assert active_decline_exists(
        db_session,
        request_row=request_row,
        user_id=agent.id,
    )

    fill_agent_capacity(db_session, user=agent)
    db_session.refresh(request_row)
    assert request_row.status == "requested"
    assert request_row.assigned_agent_id is None

    decision.expires_at = utc_now() - timedelta(seconds=1)
    db_session.flush()
    fill_agent_capacity(db_session, user=agent)
    db_session.refresh(request_row)
    assert request_row.status == "accepted"
    assert request_row.assigned_agent_id == agent.id
