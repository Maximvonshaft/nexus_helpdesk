from __future__ import annotations

import os
from datetime import timedelta

import pytest

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/agent_confirmation_voice_availability.db",
)

import app.model_registry  # noqa: F401,E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.models import Tenant, User  # noqa: E402
from app.models_agent_routing import (  # noqa: E402
    ConversationControl,
    OperatorAgentState,
)
from app.operator_models import OperatorQueueScopeGrant  # noqa: E402
from app.services.agent_availability_service import (  # noqa: E402
    availability_summary,
)
from app.services.agent_confirmation_service import (  # noqa: E402
    consume_confirmation,
    create_or_reuse_confirmation,
    resolve_confirmation_from_customer_message,
    validate_confirmation_grant,
)
from app.utils.time import utc_now  # noqa: E402
from app.voice_models import WebchatVoiceSession  # noqa: E402
from app.webchat_models import (  # noqa: E402
    WebchatConversation,
    WebchatHandoffRequest,
    WebchatMessage,
)


@pytest.fixture(autouse=True)
def schema():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


def _conversation(db, suffix: str, *, tenant_key: str = "tenant-a"):
    now = utc_now()
    row = WebchatConversation(
        public_id=f"wc_{suffix}",
        visitor_token_hash="a" * 64,
        tenant_key=tenant_key,
        channel_key="voice",
        status="open",
        last_seen_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    return row


def _visitor_message(db, conversation, suffix: str, body: str):
    row = WebchatMessage(
        conversation_id=conversation.id,
        direction="visitor",
        body=body,
        body_text=body,
        message_type="text",
        client_message_id=f"message-{suffix}",
        delivery_status="sent",
        created_at=utc_now(),
    )
    db.add(row)
    db.flush()
    return row


def test_customer_confirmation_is_exact_one_time_and_not_model_asserted():
    db = SessionLocal()
    try:
        conversation = _conversation(db, "confirmation")
        arguments = {
            "title": "Delivery follow-up",
            "description": "Please contact the customer tomorrow.",
            "priority": "normal",
        }
        confirmation = create_or_reuse_confirmation(
            db,
            conversation=conversation,
            tool_name="ticket.create",
            arguments=arguments,
        )
        db.commit()

        ambiguous = _visitor_message(
            db,
            conversation,
            "ambiguous",
            "Maybe later",
        )
        result = resolve_confirmation_from_customer_message(
            db,
            conversation=conversation,
            message=ambiguous,
        )
        assert result is not None
        assert result["decision"] == "ambiguous"
        assert confirmation.status == "pending"
        assert (
            validate_confirmation_grant(
                db,
                conversation=conversation,
                confirmation_id=confirmation.public_id,
                tool_name="ticket.create",
                arguments=arguments,
            )
            is None
        )

        confirmed_message = _visitor_message(
            db,
            conversation,
            "confirmed",
            "Yes please",
        )
        result = resolve_confirmation_from_customer_message(
            db,
            conversation=conversation,
            message=confirmed_message,
        )
        assert result is not None
        assert result["decision"] == "confirmed"
        grant = validate_confirmation_grant(
            db,
            conversation=conversation,
            confirmation_id=confirmation.public_id,
            tool_name="ticket.create",
            arguments=arguments,
        )
        assert grant is not None
        assert (
            validate_confirmation_grant(
                db,
                conversation=conversation,
                confirmation_id=confirmation.public_id,
                tool_name="ticket.create",
                arguments={**arguments, "priority": "urgent"},
            )
            is None
        )

        consume_confirmation(
            db,
            row=grant,
            tool_call_log_id=None,
        )
        db.commit()
        assert grant.status == "consumed"
        assert (
            validate_confirmation_grant(
                db,
                conversation=conversation,
                confirmation_id=confirmation.public_id,
                tool_name="ticket.create",
                arguments=arguments,
            )
            is None
        )
    finally:
        db.close()


def test_customer_denial_never_grants_tool_execution():
    db = SessionLocal()
    try:
        conversation = _conversation(db, "denial")
        arguments = {
            "title": "Callback request",
            "description": "Call the customer tomorrow.",
        }
        confirmation = create_or_reuse_confirmation(
            db,
            conversation=conversation,
            tool_name="ticket.create",
            arguments=arguments,
        )
        message = _visitor_message(
            db,
            conversation,
            "denied",
            "No thanks",
        )
        result = resolve_confirmation_from_customer_message(
            db,
            conversation=conversation,
            message=message,
        )
        db.commit()
        assert result is not None
        assert result["decision"] == "denied"
        assert confirmation.status == "denied"
        assert (
            validate_confirmation_grant(
                db,
                conversation=conversation,
                confirmation_id=confirmation.public_id,
                tool_name="ticket.create",
                arguments=arguments,
            )
            is None
        )
    finally:
        db.close()


def _control(db, conversation, *, tenant_key: str = "tenant-a"):
    row = ConversationControl(
        conversation_id=conversation.id,
        tenant_key=tenant_key,
        country_code="ME",
        channel_key="voice",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db.add(row)
    db.flush()
    return row


def _voice_session(
    db,
    conversation,
    suffix: str,
    *,
    status: str,
    accepted_at=None,
    ended_at=None,
):
    now = utc_now()
    row = WebchatVoiceSession(
        public_id=f"wv_{suffix}",
        conversation_id=conversation.id,
        provider="mock",
        provider_room_name=f"room-{suffix}",
        status=status,
        mode="sip_human",
        direction="inbound",
        started_at=accepted_at or now,
        ringing_at=accepted_at or now,
        accepted_at=accepted_at,
        active_at=accepted_at,
        ended_at=ended_at,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    return row


def _voice_agent(db, tenant):
    now = utc_now()
    user = User(
        username="voice-agent",
        display_name="Voice Agent",
        password_hash="test",
        role=UserRole.admin,
        tenant_id=tenant.id,
        is_active=True,
    )
    db.add(user)
    db.flush()
    db.add(
        OperatorAgentState(
            user_id=user.id,
            status="online",
            max_concurrent_conversations=3,
            voice_enabled=True,
            max_concurrent_voice_calls=1,
            voice_wrap_up_seconds=30,
            last_heartbeat_at=now,
            status_changed_at=now,
            created_at=now,
            updated_at=now,
        )
    )
    db.add(
        OperatorQueueScopeGrant(
            user_id=user.id,
            tenant_key=tenant.tenant_key,
            country_code="ME",
            channel_key="voice",
            enabled=True,
            granted_by=user.id,
        )
    )
    db.flush()
    return user


def test_voice_availability_uses_voice_capacity_and_real_service_history():
    db = SessionLocal()
    try:
        tenant = Tenant(
            tenant_key="tenant-a",
            display_name="Tenant A",
            is_active=True,
        )
        db.add(tenant)
        db.flush()
        agent = _voice_agent(db, tenant)

        waiting_conversation = _conversation(db, "waiting")
        _control(db, waiting_conversation)
        waiting_session = _voice_session(
            db,
            waiting_conversation,
            "waiting",
            status="ringing",
        )
        waiting_request = WebchatHandoffRequest(
            conversation_id=waiting_conversation.id,
            source="voice_call",
            trigger_type="voice_inbound",
            status="requested",
            reason_code="inbound_voice_call",
            requested_by_actor_type="provider",
            requested_at=utc_now(),
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        db.add(waiting_request)
        db.flush()
        waiting_session.handoff_request_id = waiting_request.id
        waiting_conversation.current_handoff_request_id = waiting_request.id
        waiting_conversation.handoff_status = "requested"

        active_conversation = _conversation(db, "active")
        _control(db, active_conversation)
        active_request = WebchatHandoffRequest(
            conversation_id=active_conversation.id,
            source="voice_call",
            trigger_type="voice_inbound",
            status="accepted",
            reason_code="inbound_voice_call",
            requested_by_actor_type="provider",
            accepted_by_user_id=agent.id,
            assigned_agent_id=agent.id,
            requested_at=utc_now() - timedelta(minutes=2),
            accepted_at=utc_now() - timedelta(minutes=2),
            created_at=utc_now() - timedelta(minutes=2),
            updated_at=utc_now(),
        )
        db.add(active_request)
        db.flush()
        active_session = _voice_session(
            db,
            active_conversation,
            "active",
            status="active",
            accepted_at=utc_now() - timedelta(minutes=2),
        )
        active_session.handoff_request_id = active_request.id

        now = utc_now()
        for index, seconds in enumerate((90, 120, 150, 180, 210), start=1):
            history_conversation = _conversation(db, f"history-{index}")
            _control(db, history_conversation)
            _voice_session(
                db,
                history_conversation,
                f"history-{index}",
                status="ended",
                accepted_at=now - timedelta(hours=index, seconds=seconds),
                ended_at=now - timedelta(hours=index),
            )
        db.commit()

        summary = availability_summary(
            db,
            tenant_key="tenant-a",
            country_code="ME",
            channel_key="voice",
            request_row=waiting_request,
            conversation_id=waiting_conversation.id,
        )
        assert summary["requires_voice_capacity"] is True
        assert summary["available"] is False
        assert summary["total_voice_capacity"] == 1
        assert summary["occupied_voice_capacity"] == 1
        assert summary["queue_position"] == 1
        assert summary["estimated_wait_seconds"] is not None
        assert summary["estimated_wait_seconds"] > 0
        assert summary["estimated_wait_range_seconds"]["max"] >= summary[
            "estimated_wait_range_seconds"
        ]["min"]
        assert summary["wait_estimate_sample_size"] == 5
        assert summary["wait_estimate_reason"] == (
            "recent_scoped_voice_service_time"
        )
    finally:
        db.close()


def test_voice_wait_is_not_invented_without_history_or_fresh_capacity():
    db = SessionLocal()
    try:
        tenant = Tenant(
            tenant_key="tenant-a",
            display_name="Tenant A",
            is_active=True,
        )
        db.add(tenant)
        db.flush()
        agent = _voice_agent(db, tenant)
        state = (
            db.query(OperatorAgentState)
            .filter(OperatorAgentState.user_id == agent.id)
            .one()
        )
        state.last_heartbeat_at = utc_now() - timedelta(minutes=10)

        conversation = _conversation(db, "no-history")
        _control(db, conversation)
        _voice_session(
            db,
            conversation,
            "no-history",
            status="ringing",
        )
        db.commit()

        summary = availability_summary(
            db,
            tenant_key="tenant-a",
            country_code="ME",
            channel_key="voice",
            conversation_id=conversation.id,
        )
        assert summary["requires_voice_capacity"] is True
        assert summary["available"] is False
        assert summary["available_voice_capacity"] == 0
        assert summary["estimated_wait_seconds"] is None
        assert summary["estimated_wait_range_seconds"] is None
        assert summary["wait_estimate_confidence"] == "unavailable"
        assert summary["wait_estimate_reason"] == (
            "no_eligible_voice_capacity"
        )
    finally:
        db.close()
