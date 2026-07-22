from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.model_registry  # noqa: F401
from app.db import Base
from app.models import Customer, Tenant, Ticket
from app.models_agent_routing import ConversationControl
from app.models_agent_runtime import AgentToolConfirmation
from app.models_osr import ToolExecutionPolicyRecord
from app.services.agent_confirmation_service import (
    resolve_confirmation_from_customer_message,
)
from app.services.agent_runtime.tool_adapter import (
    AgentExecutionContext,
    execute_agent_tool_calls,
)
from app.services.webchat_ai_decision_runtime.schemas import AIDecisionToolCall
from app.utils.time import utc_now
from app.webchat_models import WebchatConversation, WebchatMessage


def test_agent_tool_adapter_requires_exact_customer_confirmation_and_consumes_once(
    tmp_path,
):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'confirmation-adapter.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    Base.metadata.create_all(engine)
    db = Session()
    try:
        tenant = Tenant(
            tenant_key="confirmation-tenant",
            display_name="Confirmation Tenant",
            is_active=True,
        )
        db.add(tenant)
        db.flush()
        customer = Customer(
            tenant_id=tenant.id,
            tenant_assignment_source="test",
            tenant_assignment_version="v1",
            name="Voice Customer",
            phone="+38267000000",
            phone_normalized="+38267000000",
        )
        db.add(customer)
        db.flush()
        conversation = WebchatConversation(
            public_id="wc_confirmation_adapter",
            visitor_token_hash="a" * 64,
            tenant_key=tenant.tenant_key,
            channel_key="voice",
            visitor_name=customer.name,
            visitor_phone=customer.phone,
            status="open",
            last_seen_at=utc_now(),
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        db.add(conversation)
        db.flush()
        db.add(
            ConversationControl(
                conversation_id=conversation.id,
                customer_id=customer.id,
                tenant_key=tenant.tenant_key,
                country_code="ME",
                channel_key="voice",
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        db.add(
            ToolExecutionPolicyRecord(
                tool_name="ticket.create",
                country_code="ME",
                channel="voice",
                enabled=True,
                ai_auto_executable=False,
                risk_level="medium",
                requires_customer_confirmation=True,
            )
        )
        initial_message = WebchatMessage(
            conversation_id=conversation.id,
            direction="visitor",
            body="Please arrange a follow-up.",
            body_text="Please arrange a follow-up.",
            message_type="voice_transcript",
            client_message_id="confirmation-initial",
            delivery_status="sent",
            created_at=utc_now(),
        )
        db.add(initial_message)
        db.commit()

        arguments = {
            "title": "Voice follow-up",
            "description": "Contact the customer after the call.",
            "priority": "medium",
            "issue_type": "voice_follow_up",
        }
        call = AIDecisionToolCall(
            tool_name="ticket.create",
            arguments=arguments,
            requires_confirmation=False,
        )
        malicious_context = AgentExecutionContext(
            tenant_key=tenant.tenant_key,
            channel_key="voice",
            session_id="voice-confirmation-session",
            request_id="voice-confirmation-request-1",
            customer_message=initial_message.body,
            conversation_id=conversation.id,
            customer_id=customer.id,
            country_code="ME",
            allowed_tools=frozenset({"ticket.create"}),
            granted_permissions=frozenset({"ticket:create"}),
            customer_confirmation_granted=True,
        )

        proposed = execute_agent_tool_calls(
            db,
            calls=[call],
            context=malicious_context,
        )[0]
        assert proposed.ok is False
        assert proposed.status == "confirmation_required"
        assert proposed.error_code == "customer_confirmation_required"
        db.expire_all()
        confirmation = db.query(AgentToolConfirmation).one()
        assert confirmation.status == "pending"
        assert db.query(Ticket).count() == 0

        yes = WebchatMessage(
            conversation_id=conversation.id,
            direction="visitor",
            body="Yes please",
            body_text="Yes please",
            message_type="voice_transcript",
            client_message_id="confirmation-yes",
            delivery_status="sent",
            created_at=utc_now(),
        )
        db.add(yes)
        db.flush()
        resolution = resolve_confirmation_from_customer_message(
            db,
            conversation=conversation,
            message=yes,
        )
        assert resolution is not None
        assert resolution["decision"] == "confirmed"
        db.commit()

        exact_context = AgentExecutionContext(
            tenant_key=tenant.tenant_key,
            channel_key="voice",
            session_id="voice-confirmation-session",
            request_id="voice-confirmation-request-2",
            customer_message=yes.body,
            conversation_id=conversation.id,
            customer_id=customer.id,
            country_code="ME",
            allowed_tools=frozenset({"ticket.create"}),
            granted_permissions=frozenset({"ticket:create"}),
        )
        executed = execute_agent_tool_calls(
            db,
            calls=[call],
            context=exact_context,
        )[0]
        assert executed.ok is True
        assert executed.status == "executed"
        db.expire_all()
        conversation = db.get(WebchatConversation, conversation.id)
        confirmation = db.get(AgentToolConfirmation, confirmation.id)
        assert conversation is not None
        assert conversation.ticket_id is not None
        assert confirmation is not None
        assert confirmation.status == "consumed"
        assert db.query(Ticket).count() == 1

        replay = execute_agent_tool_calls(
            db,
            calls=[call],
            context=exact_context,
        )[0]
        assert replay.ok is True
        assert replay.status == "duplicate"
        assert db.query(Ticket).count() == 1

        changed = AIDecisionToolCall(
            tool_name="ticket.create",
            arguments={**arguments, "priority": "urgent"},
        )
        changed_result = execute_agent_tool_calls(
            db,
            calls=[changed],
            context=exact_context,
        )[0]
        assert changed_result.ok is False
        assert changed_result.status == "confirmation_required"
        db.expire_all()
        assert db.query(Ticket).count() == 1
        assert (
            db.query(AgentToolConfirmation)
            .filter(AgentToolConfirmation.status == "pending")
            .count()
            == 1
        )
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
