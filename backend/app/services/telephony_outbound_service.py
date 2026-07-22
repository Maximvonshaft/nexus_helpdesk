from __future__ import annotations

import hashlib
import json
import secrets
from datetime import timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..models import ChannelAccount, Customer, Market, Tenant, User
from ..models_agent_routing import ConversationControl
from ..utils.time import utc_now
from ..voice_models import (
    VoiceChannelConfiguration,
    VoiceRoutingOffer,
    WebchatVoiceParticipant,
    WebchatVoiceSession,
)
from ..webchat_models import WebchatConversation, WebchatEvent, WebchatHandoffRequest
from ..webchat_voice_config import load_webchat_voice_runtime_config
from .agent_routing_service import accept_voice_offer
from .identity_tenant_scope import actor_tenant_id
from .livekit_voice_provider import LiveKitVoiceProvider
from .mock_voice_provider import MockVoiceProvider
from .telephony_projection_service import _dispatch_room_agent
from .voice_command_service import enqueue_voice_command, serialize_voice_command
from .voice_provider import VoiceProvider, VoiceProviderError
from .voice_session_service import serialize_voice_session

_OUTBOUND_MODES = {"human", "ai"}


def _clean_phone(value: str) -> str:
    normalized = "".join(str(value or "").strip().split())
    if not normalized or len(normalized) > 32:
        raise HTTPException(status_code=422, detail="invalid outbound phone number")
    if any(character not in "+0123456789*#" for character in normalized):
        raise HTTPException(status_code=422, detail="invalid outbound phone number")
    return normalized


def _provider() -> VoiceProvider:
    config = load_webchat_voice_runtime_config()
    if config.provider == "mock":
        from ..settings import get_settings

        if get_settings().app_env == "production":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="livekit telephony provider is not configured",
            )
        return MockVoiceProvider()
    if config.provider == "livekit":
        try:
            return LiveKitVoiceProvider.from_config(config)
        except VoiceProviderError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="livekit telephony provider is not configured",
            ) from exc
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="livekit telephony provider is not configured",
    )


def _voice_account(
    db: Session,
    *,
    actor: User,
    channel_account_id: int,
) -> tuple[ChannelAccount, VoiceChannelConfiguration, Tenant, Market]:
    tenant_id = actor_tenant_id(db, actor)
    result = (
        db.query(ChannelAccount, VoiceChannelConfiguration)
        .join(
            VoiceChannelConfiguration,
            VoiceChannelConfiguration.channel_account_id == ChannelAccount.id,
        )
        .filter(
            ChannelAccount.id == channel_account_id,
            ChannelAccount.tenant_id == tenant_id,
            ChannelAccount.provider == "voice",
            ChannelAccount.is_active.is_(True),
            VoiceChannelConfiguration.enabled.is_(True),
        )
        .first()
    )
    if result is None:
        raise HTTPException(status_code=404, detail="voice channel account not found")
    account, configuration = result
    if not configuration.outbound_trunk_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="outbound SIP trunk is not configured",
        )
    tenant = db.get(Tenant, tenant_id)
    market = db.get(Market, account.market_id) if account.market_id is not None else None
    if tenant is None or not tenant.is_active:
        raise HTTPException(status_code=409, detail="voice channel tenant unavailable")
    if market is None or market.tenant_id != tenant_id:
        raise HTTPException(status_code=409, detail="voice channel market unavailable")
    return account, configuration, tenant, market


def _customer(
    db: Session,
    *,
    tenant: Tenant,
    phone_number: str,
) -> Customer:
    existing = (
        db.query(Customer)
        .filter(
            Customer.tenant_id == tenant.id,
            Customer.phone_normalized == phone_number,
        )
        .order_by(Customer.id.asc())
        .first()
    )
    if existing is not None:
        return existing
    now = utc_now()
    row = Customer(
        tenant_id=tenant.id,
        tenant_assignment_source="authenticated_operator",
        tenant_assignment_version="telephony.v1",
        name=f"Phone contact {phone_number[-4:]}",
        phone=phone_number,
        phone_normalized=phone_number,
        external_ref=f"voice:{hashlib.sha256(phone_number.encode('utf-8')).hexdigest()[:24]}",
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    return row


def _event(
    db: Session,
    *,
    session: WebchatVoiceSession,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    db.add(
        WebchatEvent(
            conversation_id=session.conversation_id,
            ticket_id=session.ticket_id,
            event_type=event_type,
            payload_json=json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ),
            created_at=utc_now(),
        )
    )


def _create_conversation(
    db: Session,
    *,
    tenant: Tenant,
    market: Market,
    customer: Customer,
    phone_number: str,
) -> tuple[WebchatConversation, ConversationControl]:
    now = utc_now()
    public_id = f"wc_{secrets.token_urlsafe(18)}"
    token = secrets.token_urlsafe(32)
    conversation = WebchatConversation(
        public_id=public_id,
        visitor_token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        visitor_token_expires_at=now + timedelta(hours=24),
        tenant_key=tenant.tenant_key,
        channel_key="voice",
        visitor_name=customer.name,
        visitor_phone=phone_number,
        visitor_ref=f"outbound:{public_id}",
        origin="operator_outbound_voice",
        status="open",
        handoff_status="none",
        ai_suspended=False,
        last_seen_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(conversation)
    db.flush()
    control = ConversationControl(
        conversation_id=conversation.id,
        customer_id=customer.id,
        tenant_key=tenant.tenant_key,
        country_code=market.country_code,
        channel_key="voice",
        created_at=now,
        updated_at=now,
    )
    db.add(control)
    db.flush()
    return conversation, control


def _assign_outbound_operator(
    db: Session,
    *,
    session: WebchatVoiceSession,
    conversation: WebchatConversation,
    actor: User,
    offer_timeout_seconds: int,
) -> None:
    now = utc_now()
    handoff = WebchatHandoffRequest(
        conversation_id=conversation.id,
        ticket_id=None,
        source="operator_outbound",
        trigger_type="outbound_voice_call",
        status="requested",
        reason_code="operator_outbound_call",
        reason_text="Operator initiated an outbound phone call.",
        recommended_agent_action="Join the call and speak with the customer.",
        requested_by_actor_type="operator",
        requested_by_user_id=actor.id,
        requested_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(handoff)
    db.flush()
    session.handoff_request_id = handoff.id
    conversation.current_handoff_request_id = handoff.id
    conversation.handoff_status = "requested"
    conversation.ai_suspended = True
    conversation.ai_suspended_reason = "operator_outbound_call"
    conversation.updated_at = now
    offer = VoiceRoutingOffer(
        public_id=f"vo_{secrets.token_urlsafe(18)}",
        voice_session_id=session.id,
        handoff_request_id=handoff.id,
        agent_id=actor.id,
        sequence=1,
        status="offered",
        offered_at=now,
        expires_at=now + timedelta(seconds=max(5, min(offer_timeout_seconds, 120))),
        created_at=now,
        updated_at=now,
    )
    db.add(offer)
    db.flush()
    accept_voice_offer(db, voice_session=session, user=actor)


def create_outbound_call(
    db: Session,
    *,
    actor: User,
    channel_account_id: int,
    phone_number: str,
    mode: str,
    locale: str | None = None,
) -> dict[str, Any]:
    normalized_mode = str(mode or "human").strip().lower()
    if normalized_mode not in _OUTBOUND_MODES:
        raise HTTPException(status_code=422, detail="invalid outbound call mode")
    normalized_phone = _clean_phone(phone_number)
    account, configuration, tenant, market = _voice_account(
        db,
        actor=actor,
        channel_account_id=channel_account_id,
    )
    provider = _provider()
    customer = _customer(
        db,
        tenant=tenant,
        phone_number=normalized_phone,
    )
    conversation, _control = _create_conversation(
        db,
        tenant=tenant,
        market=market,
        customer=customer,
        phone_number=normalized_phone,
    )
    now = utc_now()
    public_id = f"wv_{secrets.token_urlsafe(18)}"
    room_name = f"nexus_voice_{public_id}"[:160]
    provider.create_room(room_name=room_name)
    try:
        session = WebchatVoiceSession(
            public_id=public_id,
            conversation_id=conversation.id,
            ticket_id=None,
            channel_account_id=account.id,
            provider=provider.provider_name,
            provider_room_name=room_name,
            status="ringing",
            mode="sip_human" if normalized_mode == "human" else "sip_ai",
            direction="outbound",
            locale=locale,
            called_number=normalized_phone,
            recording_consent=configuration.recording_policy == "always",
            recording_status=(
                "requested" if configuration.recording_policy == "always" else "disabled"
            ),
            transcript_status=(
                "active" if configuration.transcription_policy == "always" else "disabled"
            ),
            summary_status="pending",
            ai_agent_status=(
                "controller_dispatching" if normalized_mode == "human" else "dispatching"
            ),
            ai_agent_started_at=now,
            started_at=now,
            ringing_at=now,
            expires_at=now + timedelta(seconds=configuration.queue_timeout_seconds),
            created_at=now,
            updated_at=now,
        )
        db.add(session)
        db.flush()
        _dispatch_room_agent(
            db,
            session=session,
            account=account,
            configuration=configuration,
        )
        participant_token = None
        participant_identity = None
        expires_in_seconds = None
        if normalized_mode == "human":
            _assign_outbound_operator(
                db,
                session=session,
                conversation=conversation,
                actor=actor,
                offer_timeout_seconds=configuration.offer_timeout_seconds,
            )
            participant_identity = f"agent_{session.public_id}_{actor.id}"[:160]
            token = provider.issue_participant_token(
                room_name=room_name,
                participant_identity=participant_identity,
                ttl_seconds=load_webchat_voice_runtime_config().session_ttl_seconds,
            )
            participant_token = token.participant_token
            expires_in_seconds = token.expires_in_seconds
            leg = (
                db.query(WebchatVoiceParticipant)
                .filter(
                    WebchatVoiceParticipant.voice_session_id == session.id,
                    WebchatVoiceParticipant.user_id == actor.id,
                    WebchatVoiceParticipant.participant_type == "human",
                )
                .first()
            )
            if leg is not None:
                leg.provider_identity = participant_identity
                leg.status = "invited"
                leg.updated_at = now
            session.status = "active"
            session.accepted_at = session.accepted_at or now
            session.active_at = session.active_at or now
        command = enqueue_voice_command(
            db,
            session=session,
            actor=actor,
            action_type="add_participant",
            target=normalized_phone,
            note="canonical_outbound_call",
            idempotency_key=f"outbound-call:{session.public_id}",
        )
        _event(
            db,
            session=session,
            event_type="voice.outbound.requested",
            payload={
                "voice_session_id": session.public_id,
                "channel_account_id": account.id,
                "mode": normalized_mode,
                "target_hash": hashlib.sha256(normalized_phone.encode("utf-8")).hexdigest(),
                "command_id": command.public_id,
                "actor_user_id": actor.id,
            },
        )
        db.flush()
        response = serialize_voice_session(
            db,
            session=session,
            participant_token=participant_token,
            expires_in_seconds=expires_in_seconds,
            participant_identity=participant_identity,
            current_agent_id=actor.id if normalized_mode == "human" else None,
        )
        response["command"] = serialize_voice_command(command)
        return response
    except Exception:
        try:
            provider.close_room(room_name=room_name)
        except Exception:
            pass
        raise
