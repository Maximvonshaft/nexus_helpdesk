from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..models import ChannelAccount, Customer, Market, Tenant
from ..models_agent_routing import ConversationControl
from ..utils.time import utc_now
from ..voice_models import (
    VoiceChannelConfiguration,
    WebchatVoiceParticipant,
    WebchatVoiceSession,
)
from ..webchat_models import WebchatConversation, WebchatEvent
from ..webchat_voice_config import load_webchat_voice_runtime_config
from .agent_routing_service import request_handoff
from .livekit_voice_provider import LiveKitVoiceProvider
from .mock_voice_provider import MockVoiceProvider
from .voice_command_dispatcher import (
    resolve_voice_command_from_provider_event,
)
from .voice_provider import VoiceProvider, VoiceProviderError
from .voice_room_control_service import (
    dispatch_room_controller,
    ensure_recording_command,
)
from .voice_session_service import _mark_terminal

logger = logging.getLogger(__name__)

_TERMINAL_SESSION_STATUSES = {"ended", "missed", "failed", "cancelled"}
_SIP_ACTIVE_STATUSES = {"active", "answered", "connected"}
_SIP_RINGING_STATUSES = {"ringing", "automation", "dialing"}
_SIP_FAILURE_STATUSES = {
    "hangup",
    "disconnected",
    "failed",
    "busy",
    "no-answer",
    "no_answer",
}


def _clean(value: Any, *, limit: int = 180) -> str | None:
    normalized = str(value or "").strip()
    return normalized[:limit] or None


def _hash(value: str | None) -> str | None:
    return (
        hashlib.sha256(value.encode("utf-8")).hexdigest()
        if value
        else None
    )


def _provider() -> VoiceProvider:
    config = load_webchat_voice_runtime_config()
    if config.provider == "mock":
        return MockVoiceProvider()
    if config.provider == "livekit":
        return LiveKitVoiceProvider.from_config(config)
    raise VoiceProviderError("voice provider is unavailable")


def _event(
    db: Session,
    *,
    session: WebchatVoiceSession,
    event_type: str,
    payload: dict[str, Any],
) -> WebchatEvent:
    row = WebchatEvent(
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
    db.add(row)
    db.flush()
    return row


def _tenant(
    account: ChannelAccount,
    db: Session,
) -> Tenant:
    row = db.get(Tenant, account.tenant_id)
    if row is None or not row.is_active:
        raise RuntimeError("voice_channel_tenant_unavailable")
    return row


def _country_code(
    account: ChannelAccount,
    db: Session,
) -> str:
    if account.market_id is None:
        raise RuntimeError("voice_channel_market_required")
    market = db.get(Market, account.market_id)
    if market is None or market.tenant_id != account.tenant_id:
        raise RuntimeError("voice_channel_market_tenant_mismatch")
    country_code = str(market.country_code or "").strip().upper()
    if not country_code:
        raise RuntimeError("voice_channel_country_required")
    return country_code


def _advisory_call_lock(
    db: Session,
    *,
    provider_call_id: str,
) -> None:
    if not (
        db.bind
        and db.bind.dialect.name.startswith("postgresql")
    ):
        return
    raw = hashlib.sha256(
        f"nexus.telephony.call\x00{provider_call_id}".encode("utf-8")
    ).digest()[:8]
    key = int.from_bytes(raw, byteorder="big", signed=True)
    db.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": key},
    )


def _customer(
    db: Session,
    *,
    tenant: Tenant,
    phone_number: str | None,
    provider_call_id: str,
) -> Customer:
    normalized_phone = _clean(phone_number, limit=60)
    row = None
    if normalized_phone:
        row = (
            db.query(Customer)
            .filter(
                Customer.tenant_id == tenant.id,
                Customer.phone_normalized == normalized_phone,
            )
            .order_by(Customer.id.asc())
            .first()
        )
    if row is not None:
        return row
    now = utc_now()
    row = Customer(
        tenant_id=tenant.id,
        tenant_assignment_source="server_provider_mapping",
        tenant_assignment_version="telephony.v1",
        name=(
            f"Caller {normalized_phone[-4:]}"
            if normalized_phone
            else "Phone caller"
        ),
        phone=normalized_phone,
        phone_normalized=normalized_phone,
        external_ref=(
            f"livekit-sip:{tenant.id}:{provider_call_id}"
        )[:120],
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    return row


def _session_by_room_or_call(
    db: Session,
    *,
    room_name: str | None,
    provider_call_id: str | None,
    lock: bool = False,
) -> WebchatVoiceSession | None:
    if provider_call_id:
        query = db.query(WebchatVoiceSession).filter(
            WebchatVoiceSession.provider == "livekit",
            WebchatVoiceSession.provider_call_id == provider_call_id,
        )
        if (
            lock
            and db.bind
            and db.bind.dialect.name.startswith("postgresql")
        ):
            query = query.with_for_update()
        row = query.order_by(WebchatVoiceSession.id.desc()).first()
        if row is not None:
            return row
    if room_name:
        query = db.query(WebchatVoiceSession).filter(
            WebchatVoiceSession.provider_room_name == room_name
        )
        if (
            lock
            and db.bind
            and db.bind.dialect.name.startswith("postgresql")
        ):
            query = query.with_for_update()
        return query.order_by(WebchatVoiceSession.id.desc()).first()
    return None


def _conversation(
    db: Session,
    *,
    account: ChannelAccount,
    provider_call_id: str,
    caller_number: str | None,
) -> tuple[WebchatConversation, ConversationControl]:
    existing_session = _session_by_room_or_call(
        db,
        room_name=None,
        provider_call_id=provider_call_id,
        lock=True,
    )
    if existing_session is not None:
        conversation = db.get(
            WebchatConversation,
            existing_session.conversation_id,
        )
        control = (
            db.query(ConversationControl)
            .filter(
                ConversationControl.conversation_id
                == existing_session.conversation_id
            )
            .first()
        )
        if conversation is None or control is None:
            raise RuntimeError("voice_conversation_projection_missing")
        return conversation, control

    tenant = _tenant(account, db)
    country_code = _country_code(account, db)
    customer = _customer(
        db,
        tenant=tenant,
        phone_number=caller_number,
        provider_call_id=provider_call_id,
    )
    now = utc_now()
    public_id = f"wc_{secrets.token_urlsafe(18)}"
    visitor_token = secrets.token_urlsafe(32)
    conversation = WebchatConversation(
        public_id=public_id,
        visitor_token_hash=hashlib.sha256(
            visitor_token.encode("utf-8")
        ).hexdigest(),
        visitor_token_expires_at=now + timedelta(hours=24),
        tenant_key=tenant.tenant_key,
        channel_key="voice",
        visitor_name=customer.name,
        visitor_phone=caller_number,
        visitor_ref=(
            f"livekit-sip:{tenant.id}:{provider_call_id}"
        )[:160],
        origin="livekit_sip",
        status="open",
        runtime_session_id=f"sip:{provider_call_id}"[:120],
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
        country_code=country_code,
        channel_key="voice",
        created_at=now,
        updated_at=now,
    )
    db.add(control)
    db.flush()
    return conversation, control


def _create_inbound_session(
    db: Session,
    *,
    account: ChannelAccount,
    configuration: VoiceChannelConfiguration,
    room_name: str,
    provider_call_id: str,
    caller_number: str | None,
    called_number: str | None,
) -> WebchatVoiceSession:
    _advisory_call_lock(
        db,
        provider_call_id=provider_call_id,
    )
    existing = _session_by_room_or_call(
        db,
        room_name=room_name,
        provider_call_id=provider_call_id,
        lock=True,
    )
    if existing is not None:
        return existing

    conversation, _control = _conversation(
        db,
        account=account,
        provider_call_id=provider_call_id,
        caller_number=caller_number,
    )
    now = utc_now()
    ai_first = configuration.routing_mode == "ai_first"
    session = WebchatVoiceSession(
        public_id=f"wv_{secrets.token_urlsafe(18)}",
        conversation_id=conversation.id,
        ticket_id=conversation.ticket_id,
        channel_account_id=account.id,
        provider="livekit",
        provider_room_name=room_name,
        provider_call_id=provider_call_id,
        status="active" if ai_first else "ringing",
        mode="sip_ai" if ai_first else "sip_human",
        direction="inbound",
        caller_number_hash=_hash(caller_number),
        called_number=called_number,
        recording_consent=(
            configuration.recording_policy == "always"
        ),
        recording_status=(
            "requested"
            if configuration.recording_policy == "always"
            else "disabled"
        ),
        transcript_status=(
            "active"
            if configuration.transcription_policy == "always"
            else "disabled"
        ),
        summary_status="pending",
        ai_agent_status=(
            "dispatching"
            if ai_first
            else "controller_dispatching"
        ),
        ai_agent_started_at=now,
        started_at=now,
        ringing_at=None if ai_first else now,
        active_at=now if ai_first else None,
        expires_at=now + timedelta(
            seconds=configuration.queue_timeout_seconds
        ),
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.flush()
    _event(
        db,
        session=session,
        event_type="voice.session.created",
        payload={
            "voice_session_id": session.public_id,
            "direction": "inbound",
            "channel_account_id": account.id,
            "provider_call_id_hash": _hash(provider_call_id),
            "routing_mode": configuration.routing_mode,
        },
    )
    dispatch_room_controller(
        db,
        session=session,
        provider=_provider(),
        agent_name=str(configuration.ai_agent_name or "").strip(),
        role="ai_controller" if ai_first else "controller",
        metadata={
            "tenant_id": account.tenant_id,
            "direction": "inbound",
        },
    )
    ensure_recording_command(
        db,
        session=session,
        actor=None,
    )
    if not ai_first:
        handoff = request_handoff(
            db,
            conversation=conversation,
            source="voice_call",
            trigger_type="sip_inbound",
            reason_code="inbound_voice_call",
            reason_text="Inbound phone call waiting for an operator.",
            recommended_agent_action=(
                "Answer the inbound phone call."
            ),
            requested_by_actor_type="provider",
        )
        session.handoff_request_id = handoff.id
    db.flush()
    return session


def _participant_attributes(
    participant: dict[str, Any] | None,
) -> dict[str, str]:
    if not isinstance(participant, dict):
        return {}
    attributes = participant.get("attributes")
    if not isinstance(attributes, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in attributes.items()
        if value is not None
    }


def _participant_metadata(
    participant: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(participant, dict):
        return {}
    raw = participant.get("metadata")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}
    return {}


def _infer_participant_type(
    *,
    session: WebchatVoiceSession,
    identity: str,
    sip_call_id: str | None,
    metadata: dict[str, Any],
) -> tuple[str, str]:
    if sip_call_id:
        return "caller", session.direction
    role = str(
        metadata.get("role")
        or metadata.get("nexus_role")
        or ""
    ).lower()
    if role == "controller":
        return "controller", "internal"
    if role in {"ai", "ai_controller"}:
        return "ai", "internal"
    if identity.startswith("agent_") or identity.startswith("agent:"):
        return "human", "internal"
    if identity.startswith("visitor_"):
        return "visitor", "inbound"
    return "service", "internal"


def _upsert_leg(
    db: Session,
    *,
    session: WebchatVoiceSession,
    participant: dict[str, Any],
    joined: bool,
) -> WebchatVoiceParticipant:
    identity = _clean(
        participant.get("identity"),
        limit=160,
    )
    if not identity:
        raise RuntimeError("provider_participant_identity_missing")
    attrs = _participant_attributes(participant)
    metadata = _participant_metadata(participant)
    sip_call_id = _clean(attrs.get("sip.callID"), limit=160)
    row = (
        db.query(WebchatVoiceParticipant)
        .filter(
            WebchatVoiceParticipant.voice_session_id == session.id,
            WebchatVoiceParticipant.provider_identity == identity,
        )
        .order_by(WebchatVoiceParticipant.id.desc())
        .first()
    )
    now = utc_now()
    if row is None:
        participant_type, direction = _infer_participant_type(
            session=session,
            identity=identity,
            sip_call_id=sip_call_id,
            metadata=metadata,
        )
        row = WebchatVoiceParticipant(
            voice_session_id=session.id,
            participant_type=participant_type,
            provider_identity=identity,
            provider_call_id=sip_call_id,
            direction=direction,
            status="joined" if joined else "ended",
            metadata_json=json.dumps(
                {
                    "role": (
                        metadata.get("role")
                        or metadata.get("nexus_role")
                    ),
                    "sip_call_id_hash": _hash(sip_call_id),
                },
                sort_keys=True,
            ),
            started_at=now,
            joined_at=now if joined else None,
            left_at=None if joined else now,
            ended_at=None if joined else now,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    elif joined and row.ended_at is None:
        row.status = "joined"
        row.joined_at = row.joined_at or now
        row.updated_at = now
    elif not joined:
        row.status = "ended"
        row.left_at = row.left_at or now
        row.ended_at = row.ended_at or now
        row.updated_at = now
    db.flush()
    return row


def _project_sip_status(
    db: Session,
    *,
    session: WebchatVoiceSession,
    call_status: str | None,
) -> None:
    normalized = str(call_status or "").strip().lower()
    if (
        not normalized
        or session.status in _TERMINAL_SESSION_STATUSES
    ):
        return
    now = utc_now()
    if normalized in _SIP_ACTIVE_STATUSES:
        session.active_at = session.active_at or now
        if (
            session.direction == "outbound"
            or session.handoff_request_id is None
            or session.mode.endswith("_ai")
        ):
            session.status = "active"
        session.updated_at = now
    elif (
        normalized in _SIP_RINGING_STATUSES
        and session.status == "created"
    ):
        session.status = "ringing"
        session.ringing_at = session.ringing_at or now
        session.updated_at = now
    elif normalized in _SIP_FAILURE_STATUSES:
        terminal_status = (
            "failed"
            if normalized
            in {"failed", "busy", "no-answer", "no_answer"}
            else "ended"
        )
        _mark_terminal(
            db,
            session=session,
            status_value=terminal_status,
            ended_by_user_id=None,
            reason=f"sip_status:{normalized}",
        )


def _event_room_name(payload: dict[str, Any]) -> str | None:
    room = payload.get("room")
    if isinstance(room, dict):
        name = _clean(room.get("name"), limit=160)
        if name:
            return name
    direct = _clean(payload.get("roomName"), limit=160)
    if direct:
        return direct
    egress = payload.get("egressInfo") or payload.get("egress")
    if isinstance(egress, dict):
        return _clean(
            egress.get("roomName") or egress.get("room_name"),
            limit=160,
        )
    return None


def project_livekit_event(
    db: Session,
    *,
    event_type: str,
    payload: dict[str, Any],
    account: ChannelAccount | None,
    configuration: VoiceChannelConfiguration | None,
) -> WebchatVoiceSession | None:
    participant = (
        payload.get("participant")
        if isinstance(payload.get("participant"), dict)
        else {}
    )
    attrs = _participant_attributes(participant)
    room_name = _event_room_name(payload)
    provider_call_id = _clean(
        attrs.get("sip.callID"),
        limit=160,
    )
    called_number = _clean(
        attrs.get("sip.trunkPhoneNumber")
        or attrs.get("sip.callTo"),
        limit=32,
    )
    caller_number = _clean(
        attrs.get("sip.phoneNumber"),
        limit=60,
    )
    call_status = _clean(
        attrs.get("sip.callStatus"),
        limit=40,
    )

    if provider_call_id:
        _advisory_call_lock(
            db,
            provider_call_id=provider_call_id,
        )
    session = _session_by_room_or_call(
        db,
        room_name=room_name,
        provider_call_id=provider_call_id,
        lock=True,
    )
    if session is None and provider_call_id and room_name:
        if account is None or configuration is None:
            return None
        session = _create_inbound_session(
            db,
            account=account,
            configuration=configuration,
            room_name=room_name,
            provider_call_id=provider_call_id,
            caller_number=caller_number,
            called_number=called_number or account.account_id,
        )
    if session is None:
        return None
    if provider_call_id and not session.provider_call_id:
        session.provider_call_id = provider_call_id
    if called_number and not session.called_number:
        session.called_number = called_number
    if caller_number and not session.caller_number_hash:
        session.caller_number_hash = _hash(caller_number)

    normalized_event = str(event_type or "").strip().lower()
    if normalized_event == "participant_joined" and participant:
        leg = _upsert_leg(
            db,
            session=session,
            participant=participant,
            joined=True,
        )
        if leg.participant_type in {"ai", "controller"}:
            session.ai_agent_status = (
                "active"
                if leg.participant_type == "ai"
                else "controller_active"
            )
            session.ai_agent_worker_id = leg.provider_identity
            session.ai_agent_last_heartbeat_at = utc_now()
        _project_sip_status(
            db,
            session=session,
            call_status=call_status,
        )
        _event(
            db,
            session=session,
            event_type="voice.participant.joined",
            payload={
                "voice_session_id": session.public_id,
                "participant_type": leg.participant_type,
                "provider_identity_hash": _hash(
                    leg.provider_identity
                ),
                "call_status": call_status,
            },
        )
    elif normalized_event == "participant_left" and participant:
        leg = _upsert_leg(
            db,
            session=session,
            participant=participant,
            joined=False,
        )
        if leg.participant_type == "caller":
            _mark_terminal(
                db,
                session=session,
                status_value="ended",
                ended_by_user_id=None,
                reason="caller_participant_left",
            )
        elif leg.participant_type in {"ai", "controller"}:
            session.ai_agent_status = "ended"
            session.ai_agent_ended_at = utc_now()
            session.updated_at = utc_now()
        _event(
            db,
            session=session,
            event_type="voice.participant.left",
            payload={
                "voice_session_id": session.public_id,
                "participant_type": leg.participant_type,
                "provider_identity_hash": _hash(
                    leg.provider_identity
                ),
            },
        )
    elif normalized_event in {"room_finished", "room_ended"}:
        _mark_terminal(
            db,
            session=session,
            status_value="ended",
            ended_by_user_id=None,
            reason=normalized_event,
        )
    elif normalized_event in {
        "egress_started",
        "egress_updated",
        "egress_ended",
    }:
        egress = payload.get("egressInfo") or payload.get("egress") or {}
        egress_id = _clean(
            egress.get("egressId") or egress.get("egress_id"),
            limit=180,
        )
        egress_status = str(
            egress.get("status") or ""
        ).strip().lower()
        session.recording_provider_ref = (
            egress_id or session.recording_provider_ref
        )
        if normalized_event == "egress_started":
            session.recording_status = "recording"
        elif normalized_event == "egress_ended":
            session.recording_status = (
                "recorded"
                if egress_status not in {"failed", "aborted"}
                else "failed"
            )
        else:
            session.recording_status = (
                egress_status[:40]
                or session.recording_status
            )
        session.updated_at = utc_now()
        _event(
            db,
            session=session,
            event_type=(
                "voice.recording."
                f"{normalized_event.removeprefix('egress_')}"
            ),
            payload={
                "voice_session_id": session.public_id,
                "provider_reference": egress_id,
                "provider_status": egress_status,
            },
        )
    else:
        _project_sip_status(
            db,
            session=session,
            call_status=call_status,
        )
    db.flush()
    return session


def project_controller_event(
    db: Session,
    *,
    payload: dict[str, Any],
) -> WebchatVoiceSession | None:
    room_name = _clean(
        payload.get("room_name"),
        limit=160,
    )
    session = _session_by_room_or_call(
        db,
        room_name=room_name,
        provider_call_id=None,
        lock=True,
    )
    if session is None:
        return None
    event_type = str(
        payload.get("event_type") or ""
    ).strip().lower()
    controller_identity = _clean(
        payload.get("controller_identity"),
        limit=160,
    )
    now = utc_now()
    if (
        event_type in {"controller.joined", "controller.heartbeat"}
        and controller_identity
    ):
        participant = {
            "identity": controller_identity,
            "metadata": {
                "role": str(payload.get("role") or "controller")
            },
            "attributes": {},
        }
        leg = _upsert_leg(
            db,
            session=session,
            participant=participant,
            joined=True,
        )
        leg.status = "joined"
        leg.joined_at = leg.joined_at or now
        leg.updated_at = now
        session.ai_agent_worker_id = controller_identity
        session.ai_agent_last_heartbeat_at = now
        session.ai_agent_status = (
            "active"
            if leg.participant_type == "ai"
            else "controller_active"
        )
        session.updated_at = now
    elif event_type == "controller.left" and controller_identity:
        participant = {
            "identity": controller_identity,
            "metadata": {
                "role": str(payload.get("role") or "controller")
            },
            "attributes": {},
        }
        _upsert_leg(
            db,
            session=session,
            participant=participant,
            joined=False,
        )
        session.ai_agent_status = "ended"
        session.ai_agent_ended_at = now
        session.updated_at = now
    elif event_type in {"command.succeeded", "command.failed"}:
        reference = _clean(
            payload.get("command_reference"),
            limit=180,
        )
        if reference:
            resolve_voice_command_from_provider_event(
                db,
                command_reference=reference,
                succeeded=(event_type == "command.succeeded"),
                provider_status=str(
                    payload.get("provider_status")
                    or event_type.split(".")[-1]
                ),
                provider_reason=_clean(
                    payload.get("provider_reason"),
                    limit=160,
                ),
                provider_result=(
                    payload.get("safe_result")
                    if isinstance(
                        payload.get("safe_result"),
                        dict,
                    )
                    else {}
                ),
            )
    elif event_type == "call.status":
        _project_sip_status(
            db,
            session=session,
            call_status=_clean(
                payload.get("call_status"),
                limit=40,
            ),
        )
    _event(
        db,
        session=session,
        event_type=f"voice.controller.{event_type or 'event'}",
        payload={
            "voice_session_id": session.public_id,
            "event_type": event_type,
            "controller_identity_hash": _hash(controller_identity),
            "command_reference": _clean(
                payload.get("command_reference"),
                limit=180,
            ),
        },
    )
    db.flush()
    return session
