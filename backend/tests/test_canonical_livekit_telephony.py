from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/canonical_livekit_telephony.db")
os.environ.setdefault("WEBCHAT_HUMAN_CALL_ENABLED", "true")
os.environ.setdefault("WEBCHAT_LIVE_AI_VOICE_ENABLED", "false")
os.environ.setdefault("WEBCHAT_VOICE_PROVIDER", "mock")

from app.db import Base, SessionLocal, engine
from app.enums import UserRole
from app.models import ChannelAccount, Market, Tenant, User
from app.models_agent_routing import OperatorAgentState
from app.operator_models import OperatorQueueScopeGrant
from app.services.agent_routing_service import decline_voice_handoff_offer
from app.services.livekit_telephony_service import process_livekit_webhook_payload
from app.services.mock_voice_provider import MockVoiceProvider
from app.utils.time import utc_now
from app.voice_models import VoiceChannelConfiguration, WebchatVoiceSession
from app.webchat_models import WebchatConversation, WebchatHandoffRequest


@pytest.fixture(autouse=True)
def schema():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


def _seed_human_voice_route(db):
    now = utc_now()
    tenant = Tenant(tenant_key="voice-test", display_name="Voice Test", is_active=True)
    db.add(tenant)
    db.flush()
    market = Market(
        tenant_id=tenant.id,
        tenant_assignment_source="test",
        tenant_assignment_version="test",
        code="ME-VOICE",
        name="Montenegro Voice",
        country_code="ME",
        is_active=True,
    )
    db.add(market)
    db.flush()
    account = ChannelAccount(
        tenant_id=tenant.id,
        tenant_assignment_source="test",
        tenant_assignment_version="test",
        provider="voice",
        account_id="+38220000111",
        display_name="ME Support",
        market_id=market.id,
        is_active=True,
        health_status="configured",
    )
    db.add(account)
    db.flush()
    db.add(
        VoiceChannelConfiguration(
            channel_account_id=account.id,
            inbound_trunk_id="ST_TEST_ME",
            outbound_trunk_id="ST_OUT_ME",
            routing_mode="human_first",
            queue_timeout_seconds=90,
            wrap_up_seconds=30,
            recording_policy="disabled",
            enabled=True,
        )
    )
    agent = User(
        username="voice_agent",
        display_name="Voice Agent",
        password_hash="test",
        role=UserRole.admin,
        tenant_id=tenant.id,
        is_active=True,
    )
    db.add(agent)
    db.flush()
    db.add(
        OperatorAgentState(
            user_id=agent.id,
            status="online",
            max_concurrent_conversations=3,
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
            user_id=agent.id,
            tenant_key=tenant.tenant_key,
            country_code="ME",
            channel_key="voice",
            enabled=True,
            granted_by=agent.id,
        )
    )
    db.flush()
    return agent


def test_livekit_sip_event_creates_ticketless_conversation_and_canonical_offer():
    db = SessionLocal()
    try:
        agent = _seed_human_voice_route(db)
        payload = {
            "id": "EVT-1",
            "event": "participant_joined",
            "room": {"name": "sip-room-me-1"},
            "participant": {
                "identity": "sip-caller-1",
                "attributes": {
                    "sip.phoneNumber": "+38267000111",
                    "sip.trunkPhoneNumber": "+38220000111",
                    "sip.trunkID": "ST_TEST_ME",
                },
            },
        }
        result = process_livekit_webhook_payload(
            db,
            payload=payload,
            raw_body=json.dumps(payload, sort_keys=True).encode("utf-8"),
        )
        db.commit()
        assert result["ok"] is True
        session = db.query(WebchatVoiceSession).one()
        conversation = db.query(WebchatConversation).one()
        handoff = db.query(WebchatHandoffRequest).one()
        assert conversation.ticket_id is None
        assert session.ticket_id is None
        assert session.handoff_request_id == handoff.id
        assert handoff.status == "accepted"
        assert handoff.assigned_agent_id == agent.id
        assert conversation.active_agent_id == agent.id
        assert session.accepted_by_user_id == agent.id
        assert session.status == "ringing"
        assert session.caller_number_hash
        assert session.called_number == "+38220000111"
    finally:
        db.close()


def test_voice_decline_returns_offer_to_queue_without_hanging_up_customer():
    db = SessionLocal()
    try:
        agent = _seed_human_voice_route(db)
        payload = {
            "id": "EVT-2",
            "event": "participant_joined",
            "room": {"name": "sip-room-me-2"},
            "participant": {
                "identity": "sip-caller-2",
                "attributes": {
                    "sip.phoneNumber": "+38267000222",
                    "sip.trunkPhoneNumber": "+38220000111",
                    "sip.trunkID": "ST_TEST_ME",
                },
            },
        }
        process_livekit_webhook_payload(
            db,
            payload=payload,
            raw_body=json.dumps(payload, sort_keys=True).encode("utf-8"),
        )
        session = db.query(WebchatVoiceSession).one()
        decline_voice_handoff_offer(db, voice_session=session, user=agent, note="busy")
        db.commit()
        db.refresh(session)
        handoff = db.get(WebchatHandoffRequest, session.handoff_request_id)
        assert session.status == "ringing"
        assert session.ended_at is None
        assert session.accepted_by_user_id is None
        assert handoff is not None
        assert handoff.status == "requested"
        assert handoff.assigned_agent_id is None
    finally:
        db.close()


def test_mock_provider_executes_idempotent_command_contract():
    result = MockVoiceProvider().execute_action(
        room_name="room-1",
        action_type="keypad",
        digits="12#",
        idempotency_key="idem-1",
    )
    assert result.status == "succeeded"
    assert result.provider_status == "executed"
    assert result.safe_payload == {
        "target_present": False,
        "digits_length": 3,
        "participant_identity_present": False,
    }
