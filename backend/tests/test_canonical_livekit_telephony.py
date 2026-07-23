from __future__ import annotations

import json
import os

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
from app.services.agent_routing_service import decline_voice_offer
from app.services.mock_voice_provider import MockVoiceProvider
from app.services.telephony_event_service import process_livekit_webhook_event
from app.utils.time import utc_now
from app.voice_models import (
    VoiceChannelConfiguration,
    VoiceRoutingOffer,
    WebchatVoiceParticipant,
    WebchatVoiceSession,
)
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
            dispatch_rule_id="SDR_TEST_ME",
            routing_mode="human_first",
            ai_agent_name="nexus-voice-controller",
            queue_timeout_seconds=90,
            offer_timeout_seconds=20,
            wrap_up_seconds=30,
            recording_policy="disabled",
            transcription_policy="disabled",
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


def _inbound_payload(*, event_id: str, room_name: str, caller: str) -> dict:
    return {
        "id": event_id,
        "event": "participant_joined",
        "room": {"name": room_name},
        "participant": {
            "identity": f"sip-caller-{event_id}",
            "attributes": {
                "sip.phoneNumber": caller,
                "sip.trunkPhoneNumber": "+38220000111",
                "sip.trunkID": "ST_TEST_ME",
                "sip.ruleID": "SDR_TEST_ME",
                "sip.callID": f"call-{event_id}",
                "sip.callStatus": "active",
            },
        },
    }


def test_livekit_sip_event_creates_ticketless_conversation_and_ringing_offer():
    db = SessionLocal()
    try:
        agent = _seed_human_voice_route(db)
        payload = _inbound_payload(
            event_id="EVT-1",
            room_name="sip-room-me-1",
            caller="+38267000111",
        )
        result = process_livekit_webhook_event(
            db,
            payload=payload,
            raw_body=json.dumps(payload, sort_keys=True).encode("utf-8"),
        )
        db.commit()

        assert result["ok"] is True
        session = db.query(WebchatVoiceSession).one()
        conversation = db.query(WebchatConversation).one()
        handoff = db.query(WebchatHandoffRequest).one()
        offer = db.query(VoiceRoutingOffer).one()

        assert conversation.ticket_id is None
        assert session.ticket_id is None
        assert session.handoff_request_id == handoff.id
        assert handoff.status == "requested"
        assert handoff.assigned_agent_id is None
        assert conversation.active_agent_id is None
        assert offer.status == "offered"
        assert offer.agent_id == agent.id
        assert session.status == "ringing"
        assert session.ended_at is None
        assert session.caller_number_hash
        assert session.called_number == "+38220000111"
    finally:
        db.close()


def test_voice_decline_rotates_offer_without_hanging_up_customer():
    db = SessionLocal()
    try:
        agent = _seed_human_voice_route(db)
        payload = _inbound_payload(
            event_id="EVT-2",
            room_name="sip-room-me-2",
            caller="+38267000222",
        )
        process_livekit_webhook_event(
            db,
            payload=payload,
            raw_body=json.dumps(payload, sort_keys=True).encode("utf-8"),
        )
        session = db.query(WebchatVoiceSession).one()
        decline_voice_offer(
            db,
            voice_session=session,
            user=agent,
            note="busy",
        )
        db.commit()
        db.refresh(session)

        handoff = db.get(WebchatHandoffRequest, session.handoff_request_id)
        offer = db.query(VoiceRoutingOffer).one()
        assert session.status == "ringing"
        assert session.ended_at is None
        assert handoff is not None
        assert handoff.status == "requested"
        assert handoff.assigned_agent_id is None
        assert offer.status == "declined"
    finally:
        db.close()


def _process_provider_event(db, payload: dict) -> dict:
    return process_livekit_webhook_event(
        db,
        payload=payload,
        raw_body=json.dumps(payload, sort_keys=True).encode("utf-8"),
    )


def _sip_participant_event(
    *,
    event_id: str,
    event_type: str,
    room_name: str,
    identity: str,
    call_id: str,
    call_status: str,
    phone_number: str,
) -> dict:
    return {
        "id": event_id,
        "event": event_type,
        "room": {"name": room_name},
        "participant": {
            "identity": identity,
            "attributes": {
                "sip.phoneNumber": phone_number,
                "sip.callTo": phone_number,
                "sip.callID": call_id,
                "sip.callStatus": call_status,
            },
        },
    }


def test_secondary_sip_transfer_leg_cannot_terminate_primary_customer_call():
    db = SessionLocal()
    try:
        _seed_human_voice_route(db)
        room_name = "sip-room-me-transfer"
        inbound = _inbound_payload(
            event_id="EVT-TRANSFER",
            room_name=room_name,
            caller="+38267000333",
        )
        _process_provider_event(db, inbound)
        db.commit()

        session = db.query(WebchatVoiceSession).one()
        primary_call_id = session.provider_call_id
        assert primary_call_id == "call-EVT-TRANSFER"
        assert session.status == "ringing"

        secondary_joined = _sip_participant_event(
            event_id="EVT-TRANSFER-SECONDARY-JOINED",
            event_type="participant_joined",
            room_name=room_name,
            identity="sip-transfer-target",
            call_id="call-transfer-target",
            call_status="active",
            phone_number="+38267000999",
        )
        _process_provider_event(db, secondary_joined)

        secondary_busy = _sip_participant_event(
            event_id="EVT-TRANSFER-SECONDARY-BUSY",
            event_type="participant_updated",
            room_name=room_name,
            identity="sip-transfer-target",
            call_id="call-transfer-target",
            call_status="busy",
            phone_number="+38267000999",
        )
        _process_provider_event(db, secondary_busy)

        secondary_left = _sip_participant_event(
            event_id="EVT-TRANSFER-SECONDARY-LEFT",
            event_type="participant_left",
            room_name=room_name,
            identity="sip-transfer-target",
            call_id="call-transfer-target",
            call_status="disconnected",
            phone_number="+38267000999",
        )
        _process_provider_event(db, secondary_left)
        db.commit()
        db.refresh(session)

        transfer_leg = (
            db.query(WebchatVoiceParticipant)
            .filter(
                WebchatVoiceParticipant.voice_session_id == session.id,
                WebchatVoiceParticipant.provider_call_id == "call-transfer-target",
            )
            .one()
        )
        assert transfer_leg.participant_type == "transfer"
        assert transfer_leg.direction == "outbound"
        assert transfer_leg.status == "ended"
        assert session.provider_call_id == primary_call_id
        assert session.status == "ringing"
        assert session.ended_at is None

        primary_left = _sip_participant_event(
            event_id="EVT-TRANSFER-PRIMARY-LEFT",
            event_type="participant_left",
            room_name=room_name,
            identity="sip-caller-EVT-TRANSFER",
            call_id=primary_call_id,
            call_status="disconnected",
            phone_number="+38267000333",
        )
        _process_provider_event(db, primary_left)
        db.commit()
        db.refresh(session)

        assert session.status == "ended"
        assert session.ended_at is not None
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
        "controller_identity_present": False,
        "recording_reference_present": False,
    }
    assert "12#" not in json.dumps(result.safe_payload)
