from __future__ import annotations

import hashlib
import json
import secrets
from datetime import timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import ChannelAccount, Customer, Market, Tenant
from ..models_agent_routing import ConversationControl
from ..utils.time import utc_now
from ..voice_models import (
    TelephonyEventInbox,
    VoiceChannelConfiguration,
    WebchatVoiceParticipant,
    WebchatVoiceSession,
)
from ..webchat_models import WebchatConversation
from .agent_routing_service import request_handoff
from .audit_service import log_admin_audit
from .livekit_voice_provider import LiveKitVoiceProvider
from .webchat_ai_turn_service import safe_write_webchat_event


def _clean_phone(value: Any) -> str | None:
    text = "".join(ch for ch in str(value or "").strip() if ch.isdigit() or ch == "+")
    return text[:32] or None


def _phone_hash(value: str | None) -> str | None:
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else None


def _masked_caller(value: str | None) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return f"Phone caller ••••{digits[-4:]}" if digits else "Phone caller"


def serialize_voice_configuration(
    row: VoiceChannelConfiguration,
    account: ChannelAccount,
) -> dict[str, Any]:
    return {
        "id": row.id,
        "channel_account_id": row.channel_account_id,
        "account_id": account.account_id,
        "display_name": account.display_name,
        "market_id": account.market_id,
        "tenant_id": account.tenant_id,
        "inbound_trunk_id": row.inbound_trunk_id,
        "outbound_trunk_id": row.outbound_trunk_id,
        "routing_mode": row.routing_mode,
        "ai_agent_name": row.ai_agent_name,
        "queue_timeout_seconds": row.queue_timeout_seconds,
        "wrap_up_seconds": row.wrap_up_seconds,
        "recording_policy": row.recording_policy,
        "enabled": row.enabled,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def list_voice_configurations(db: Session) -> list[dict[str, Any]]:
    rows = (
        db.query(VoiceChannelConfiguration, ChannelAccount)
        .join(ChannelAccount, ChannelAccount.id == VoiceChannelConfiguration.channel_account_id)
        .filter(ChannelAccount.provider == "voice")
        .order_by(ChannelAccount.priority.asc(), ChannelAccount.id.asc())
        .all()
    )
    return [serialize_voice_configuration(row, account) for row, account in rows]


def upsert_voice_configuration(
    db: Session,
    *,
    actor_id: int,
    channel_account_id: int,
    inbound_trunk_id: str | None,
    outbound_trunk_id: str | None,
    routing_mode: str,
    ai_agent_name: str | None,
    queue_timeout_seconds: int,
    wrap_up_seconds: int,
    recording_policy: str,
    enabled: bool,
) -> dict[str, Any]:
    account = db.get(ChannelAccount, channel_account_id)
    if account is None or account.provider != "voice":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="voice channel account not found")
    if routing_mode not in {"ai_first", "human_first"}:
        raise HTTPException(status_code=422, detail="invalid voice routing mode")
    if recording_policy not in {"disabled", "consent_required"}:
        raise HTTPException(status_code=422, detail="invalid voice recording policy")
    if not 15 <= int(queue_timeout_seconds) <= 3600:
        raise HTTPException(status_code=422, detail="invalid voice queue timeout")
    if not 0 <= int(wrap_up_seconds) <= 900:
        raise HTTPException(status_code=422, detail="invalid voice wrap-up")
    if enabled and not (inbound_trunk_id or "").strip():
        raise HTTPException(status_code=422, detail="inbound trunk is required for an enabled voice channel")
    if enabled and routing_mode == "ai_first" and not (ai_agent_name or "").strip():
        raise HTTPException(status_code=422, detail="AI Agent name is required for AI-first routing")

    row = (
        db.query(VoiceChannelConfiguration)
        .filter(VoiceChannelConfiguration.channel_account_id == account.id)
        .with_for_update()
        .first()
    )
    old_value = serialize_voice_configuration(row, account) if row else None
    now = utc_now()
    if row is None:
        row = VoiceChannelConfiguration(channel_account_id=account.id, created_at=now)
        db.add(row)
    row.inbound_trunk_id = (inbound_trunk_id or "").strip() or None
    row.outbound_trunk_id = (outbound_trunk_id or "").strip() or None
    row.routing_mode = routing_mode
    row.ai_agent_name = (ai_agent_name or "").strip() or None
    row.queue_timeout_seconds = int(queue_timeout_seconds)
    row.wrap_up_seconds = int(wrap_up_seconds)
    row.recording_policy = recording_policy
    row.enabled = bool(enabled)
    row.updated_at = now
    account.health_status = "configured" if row.enabled else "disabled"
    account.updated_at = now
    db.flush()
    result = serialize_voice_configuration(row, account)
    log_admin_audit(
        db,
        actor_id=actor_id,
        action="telephony.voice_configuration.updated",
        target_type="voice_channel_configuration",
        target_id=row.id,
        old_value=old_value,
        new_value=result,
    )
    return result


def _configuration_by_called_number(
    db: Session,
    called_number: str | None,
    inbound_trunk_id: str | None,
) -> tuple[VoiceChannelConfiguration, ChannelAccount, Tenant, Market | None] | None:
    query = (
        db.query(VoiceChannelConfiguration, ChannelAccount, Tenant, Market)
        .join(ChannelAccount, ChannelAccount.id == VoiceChannelConfiguration.channel_account_id)
        .join(Tenant, Tenant.id == ChannelAccount.tenant_id)
        .outerjoin(Market, Market.id == ChannelAccount.market_id)
        .filter(
            ChannelAccount.provider == "voice",
            ChannelAccount.is_active.is_(True),
            Tenant.is_active.is_(True),
            VoiceChannelConfiguration.enabled.is_(True),
        )
    )
    if called_number:
        exact = query.filter(ChannelAccount.account_id == called_number).first()
        if exact is not None:
            return exact
    if inbound_trunk_id:
        trunk = query.filter(VoiceChannelConfiguration.inbound_trunk_id == inbound_trunk_id).first()
        if trunk is not None:
            return trunk
    return None


def _event_value(payload: dict[str, Any], *paths: tuple[str, ...]) -> Any:
    for path in paths:
        value: Any = payload
        for segment in path:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(segment)
        if value not in (None, ""):
            return value
    return None


def _safe_event_payload(
    *,
    event_type: str,
    room_name: str | None,
    participant_identity: str | None,
    called_number: str | None,
    caller_number: str | None,
) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "room_name": room_name,
        "participant_identity": participant_identity,
        "called_number": called_number,
        "caller_number_sha256": _phone_hash(caller_number),
    }


def _create_sip_conversation(
    db: Session,
    *,
    configuration: VoiceChannelConfiguration,
    account: ChannelAccount,
    tenant: Tenant,
    market: Market | None,
    room_name: str,
    participant_identity: str,
    caller_number: str | None,
    called_number: str | None,
) -> WebchatVoiceSession:
    now = utc_now()
    public_id = f"wc_{secrets.token_urlsafe(18).replace('-', '').replace('_', '')[:24]}"
    token_material = secrets.token_urlsafe(32)
    customer = Customer(
        tenant_id=tenant.id,
        tenant_assignment_source="livekit_sip",
        tenant_assignment_version="nexus.telephony.v1",
        name=_masked_caller(caller_number),
        phone=caller_number,
        phone_normalized=caller_number,
        external_ref=f"sip:{_phone_hash(caller_number) or participant_identity}",
        created_at=now,
        updated_at=now,
    )
    db.add(customer)
    db.flush()
    conversation = WebchatConversation(
        public_id=public_id,
        visitor_token_hash=hashlib.sha256(token_material.encode("utf-8")).hexdigest(),
        visitor_token_expires_at=None,
        tenant_key=tenant.tenant_key,
        channel_key="voice",
        ticket_id=None,
        visitor_name=customer.name,
        visitor_phone=caller_number,
        visitor_ref=customer.external_ref,
        origin="livekit_sip",
        user_agent="LiveKit SIP",
        status="open",
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
        country_code=market.country_code if market else None,
        channel_key="voice",
        created_at=now,
        updated_at=now,
    )
    if not control.country_code:
        raise HTTPException(status_code=409, detail="voice channel market country is required")
    db.add(control)
    db.flush()

    ai_first = configuration.routing_mode == "ai_first"
    session = WebchatVoiceSession(
        public_id=f"wv_{secrets.token_urlsafe(18).replace('-', '').replace('_', '')[:24]}",
        conversation_id=conversation.id,
        ticket_id=None,
        provider="livekit",
        provider_room_name=room_name,
        provider_call_id=participant_identity,
        status="active" if ai_first else "ringing",
        mode="sip_ai" if ai_first else "sip_human",
        direction="inbound",
        caller_number_hash=_phone_hash(caller_number),
        called_number=called_number or account.account_id,
        recording_consent=False,
        recording_status="disabled",
        transcript_status="active" if ai_first else "disabled",
        summary_status="pending",
        ai_agent_status="dispatching" if ai_first else None,
        ai_agent_started_at=now if ai_first else None,
        started_at=now,
        ringing_at=None if ai_first else now,
        active_at=now if ai_first else None,
        expires_at=now + timedelta(seconds=configuration.queue_timeout_seconds),
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.flush()
    db.add(
        WebchatVoiceParticipant(
            voice_session_id=session.id,
            participant_type="visitor",
            visitor_label=customer.name,
            provider_identity=participant_identity,
            status="joined",
            joined_at=now,
            created_at=now,
        )
    )
    safe_write_webchat_event(
        db,
        conversation_id=conversation.id,
        ticket_id=None,
        event_type="voice.sip.inbound",
        payload={
            "voice_session_id": session.public_id,
            "channel_account_id": account.id,
            "called_number": session.called_number,
            "caller_number_sha256": session.caller_number_hash,
            "routing_mode": configuration.routing_mode,
        },
    )
    if ai_first:
        provider = LiveKitVoiceProvider.from_config(__import__(
            "app.webchat_voice_config",
            fromlist=["load_webchat_voice_runtime_config"],
        ).load_webchat_voice_runtime_config())
        dispatch = provider.dispatch_agent(
            room_name=room_name,
            agent_name=configuration.ai_agent_name or "nexus-voice-agent",
            metadata={
                "schema": "nexus.livekit-agent-session.v1",
                "voice_session_id": session.public_id,
                "conversation_id": conversation.public_id,
                "tenant_key": tenant.tenant_key,
                "country_code": control.country_code,
                "channel_key": control.channel_key,
            },
        )
        session.ai_agent_status = dispatch.provider_status
        safe_write_webchat_event(
            db,
            conversation_id=conversation.id,
            ticket_id=None,
            event_type="voice.ai_agent.dispatched",
            payload={
                "voice_session_id": session.public_id,
                "provider_reference": dispatch.provider_reference,
            },
        )
    else:
        handoff = request_handoff(
            db,
            conversation=conversation,
            source="sip_inbound",
            trigger_type="voice_inbound",
            reason_code="inbound_phone_call",
            reason_text="Inbound phone call is waiting for an operator.",
            recommended_agent_action="Answer the inbound phone call.",
            requested_by_actor_type="provider",
        )
        session.handoff_request_id = handoff.id
        session.accepted_by_user_id = handoff.assigned_agent_id
    db.flush()
    return session


def process_livekit_webhook_payload(
    db: Session,
    *,
    payload: dict[str, Any],
    raw_body: bytes,
) -> dict[str, Any]:
    event_type = str(payload.get("event") or payload.get("event_type") or "unknown")[:80]
    provider_event_id = str(
        payload.get("id")
        or payload.get("event_id")
        or hashlib.sha256(raw_body).hexdigest()
    )[:180]
    room_name = str(_event_value(payload, ("room", "name"), ("room_name",)) or "")[:160] or None
    participant_identity = str(
        _event_value(payload, ("participant", "identity"), ("participant_identity",)) or ""
    )[:160] or None
    attributes = _event_value(payload, ("participant", "attributes"), ("attributes",)) or {}
    if not isinstance(attributes, dict):
        attributes = {}
    caller_number = _clean_phone(
        attributes.get("sip.phoneNumber")
        or attributes.get("sip.fromPhoneNumber")
        or payload.get("caller_number")
    )
    called_number = _clean_phone(
        attributes.get("sip.trunkPhoneNumber")
        or attributes.get("sip.toPhoneNumber")
        or payload.get("called_number")
    )
    inbound_trunk_id = str(
        attributes.get("sip.trunkID") or payload.get("inbound_trunk_id") or ""
    )[:160] or None
    safe_payload = _safe_event_payload(
        event_type=event_type,
        room_name=room_name,
        participant_identity=participant_identity,
        called_number=called_number,
        caller_number=caller_number,
    )
    existing = (
        db.query(TelephonyEventInbox)
        .filter(
            TelephonyEventInbox.provider == "livekit",
            TelephonyEventInbox.provider_event_id == provider_event_id,
        )
        .first()
    )
    if existing is not None:
        return {
            "ok": existing.status in {"processed", "ignored"},
            "idempotent": True,
            "status": existing.status,
            "voice_session_id": existing.voice_session_id,
        }
    inbox = TelephonyEventInbox(
        provider="livekit",
        provider_event_id=provider_event_id,
        event_type=event_type,
        payload_sha256=hashlib.sha256(raw_body).hexdigest(),
        safe_payload_json=json.dumps(safe_payload, ensure_ascii=False, sort_keys=True),
        status="received",
        attempt_count=1,
        received_at=utc_now(),
    )
    db.add(inbox)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(TelephonyEventInbox)
            .filter(
                TelephonyEventInbox.provider == "livekit",
                TelephonyEventInbox.provider_event_id == provider_event_id,
            )
            .first()
        )
        return {
            "ok": bool(existing and existing.status in {"processed", "ignored"}),
            "idempotent": True,
            "status": existing.status if existing else "received",
            "voice_session_id": existing.voice_session_id if existing else None,
        }

    try:
        session = None
        if event_type in {"participant_joined", "participant_joined_event"} and room_name and participant_identity:
            session = (
                db.query(WebchatVoiceSession)
                .filter(
                    WebchatVoiceSession.provider == "livekit",
                    WebchatVoiceSession.provider_call_id == participant_identity,
                )
                .first()
            )
            if session is None and (caller_number or called_number or inbound_trunk_id):
                route = _configuration_by_called_number(db, called_number, inbound_trunk_id)
                if route is None:
                    inbox.status = "ignored"
                    inbox.last_error_code = "voice_route_not_found"
                else:
                    configuration, account, tenant, market = route
                    session = _create_sip_conversation(
                        db,
                        configuration=configuration,
                        account=account,
                        tenant=tenant,
                        market=market,
                        room_name=room_name,
                        participant_identity=participant_identity,
                        caller_number=caller_number,
                        called_number=called_number,
                    )
        elif event_type in {"participant_left", "room_finished"}:
            filters = [WebchatVoiceSession.provider == "livekit"]
            if participant_identity:
                filters.append(WebchatVoiceSession.provider_call_id == participant_identity)
            elif room_name:
                filters.append(WebchatVoiceSession.provider_room_name == room_name)
            session = db.query(WebchatVoiceSession).filter(*filters).order_by(WebchatVoiceSession.id.desc()).first()
            if session is not None and session.ended_at is None:
                from .webchat_voice_service import _end_voice_session
                _end_voice_session(db, session=session, ended_by_user_id=None)
        if session is not None:
            inbox.voice_session_id = session.id
        if inbox.status == "received":
            inbox.status = "processed" if session is not None else "ignored"
        inbox.processed_at = utc_now()
        db.flush()
        return {
            "ok": True,
            "idempotent": False,
            "status": inbox.status,
            "voice_session_id": session.public_id if session else None,
        }
    except Exception as exc:
        inbox.status = "failed"
        inbox.last_error_code = type(exc).__name__[:120]
        inbox.processed_at = utc_now()
        db.flush()
        raise
