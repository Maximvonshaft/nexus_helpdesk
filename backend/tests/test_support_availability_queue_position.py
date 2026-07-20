from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/nexus_support_availability_position_tests.db",
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
from app.services.nexus_osr.case_context import CaseContext  # noqa: E402
from app.services.nexus_osr.controlled_action_executor import (  # noqa: E402
    ActionExecutionRequest,
)
from app.services.nexus_osr.runtime_decision_contract import (  # noqa: E402
    RuntimeToolAction,
)
from app.services.nexus_osr.tool_execution_service import (  # noqa: E402
    _production_handlers,
)
from app.webchat_models import WebchatConversation  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "support_availability_queue_position.db"
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


def _conversation(db_session, *, suffix: str) -> tuple[WebchatConversation, Customer]:
    customer = Customer(
        name=f"Customer {suffix}",
        external_ref=f"customer-{suffix}",
    )
    db_session.add(customer)
    db_session.flush()
    conversation = WebchatConversation(
        public_id=f"availability-{suffix}",
        visitor_token_hash=f"availability-hash-{suffix}",
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
    return conversation, customer


def test_support_availability_reports_current_customer_queue_position(db_session):
    agent = User(
        username="availability-agent",
        display_name="Availability Agent",
        password_hash="not-used",
        role=UserRole.agent,
        is_active=True,
    )
    db_session.add(agent)
    db_session.flush()
    db_session.add(
        OperatorQueueScopeGrant(
            user_id=agent.id,
            tenant_key="default",
            country_code="ME",
            channel_key="webchat",
            enabled=True,
        )
    )
    db_session.flush()
    set_agent_state(
        db_session,
        user=agent,
        presence_status="online",
        max_concurrent_conversations=1,
    )

    occupied, _occupied_customer = _conversation(db_session, suffix="occupied")
    first_waiting, _first_customer = _conversation(db_session, suffix="first")
    second_waiting, second_customer = _conversation(db_session, suffix="second")
    occupied_request = request_handoff(
        db_session,
        conversation=occupied,
        source="ai_runtime",
        trigger_type="runtime_handoff",
        reason_code="occupied",
    )
    first_request = request_handoff(
        db_session,
        conversation=first_waiting,
        source="ai_runtime",
        trigger_type="runtime_handoff",
        reason_code="first_waiting",
    )
    second_request = request_handoff(
        db_session,
        conversation=second_waiting,
        source="ai_runtime",
        trigger_type="runtime_handoff",
        reason_code="second_waiting",
    )
    assert occupied_request.status == "accepted"
    assert first_request.status == "requested"
    assert second_request.status == "requested"

    handler = _production_handlers(
        db_session,
        conversation=second_waiting,
        ticket=None,
        customer=second_customer,
    )["support.availability"]
    result = handler(
        ActionExecutionRequest(
            action=RuntimeToolAction(
                tool_name="support.availability",
                arguments={},
                requires_confirmation=False,
                executed=False,
            ),
            channel="webchat",
            country_code="ME",
            case_context=CaseContext(
                conversation_id=second_waiting.id,
                channel="webchat",
                country_code="ME",
            ),
        )
    )

    assert result.ok is True
    assert result.status == "executed"
    assert result.summary["online_agents"] == 1
    assert result.summary["available_capacity"] == 0
    assert result.summary["queue_count"] == 2
    assert result.summary["queue_position"] == 2
    assert result.customer_visible_summary == (
        "Human support is currently at capacity with "
        "1 conversation(s) ahead of this customer."
    )
