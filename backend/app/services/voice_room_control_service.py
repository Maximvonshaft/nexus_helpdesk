from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from ..models import User
from ..utils.time import utc_now
from ..voice_models import WebchatVoiceParticipant, WebchatVoiceSession
from ..webchat_models import WebchatConversation, WebchatEvent
from .voice_command_service import enqueue_voice_command
from .voice_provider import VoiceProvider

_CONTROLLER_ROLES = {"controller", "ai_controller"}
_RESERVED_METADATA_KEYS = {
    "schema",
    "role",
    "voice_session_id",
    "conversation_id",
    "conversation_public_id",
    "channel_account_id",
}


def _write_event(
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


def dispatch_room_controller(
    db: Session,
    *,
    session: WebchatVoiceSession,
    provider: VoiceProvider,
    agent_name: str,
    role: str,
    metadata: dict[str, Any] | None = None,
) -> WebchatVoiceParticipant:
    normalized_agent = str(agent_name or "").strip()
    normalized_role = str(role or "").strip().lower()
    if not normalized_agent:
        raise RuntimeError("telephony_controller_agent_missing")
    if normalized_role not in _CONTROLLER_ROLES:
        raise RuntimeError("invalid_telephony_controller_role")

    participant_type = "ai" if normalized_role == "ai_controller" else "controller"
    existing = (
        db.query(WebchatVoiceParticipant)
        .filter(
            WebchatVoiceParticipant.voice_session_id == session.id,
            WebchatVoiceParticipant.participant_type == participant_type,
            WebchatVoiceParticipant.status.in_(["invited", "joined"]),
            WebchatVoiceParticipant.ended_at.is_(None),
        )
        .order_by(WebchatVoiceParticipant.id.desc())
        .first()
    )
    if existing is not None:
        return existing

    conversation = db.get(WebchatConversation, session.conversation_id)
    if conversation is None or not conversation.public_id:
        raise RuntimeError("telephony_conversation_authority_missing")
    extra_metadata = {
        str(key)[:80]: value
        for key, value in dict(metadata or {}).items()
        if str(key) not in _RESERVED_METADATA_KEYS
    }
    dispatch_metadata = {
        **extra_metadata,
        "schema": "nexus.livekit-agent-session.v1",
        "role": normalized_role,
        "voice_session_id": session.public_id,
        "conversation_public_id": conversation.public_id,
        "channel_account_id": session.channel_account_id,
    }
    dispatch = provider.dispatch_agent(
        room_name=session.provider_room_name,
        agent_name=normalized_agent,
        metadata=dispatch_metadata,
    )
    now = utc_now()
    provider_reference = str(dispatch.provider_reference or "").strip()
    planned_identity = (
        f"dispatch:{provider_reference}"
        if provider_reference
        else f"dispatch:{session.public_id}:{normalized_role}"
    )[:160]
    leg = WebchatVoiceParticipant(
        voice_session_id=session.id,
        participant_type=participant_type,
        provider_identity=planned_identity,
        direction="internal",
        status="invited",
        metadata_json=json.dumps(
            {
                "dispatch_reference": provider_reference or None,
                "role": normalized_role,
                "agent_name": normalized_agent,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        started_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(leg)
    session.ai_agent_status = (
        dispatch.provider_status
        if normalized_role == "ai_controller"
        else "controller_dispatching"
    )
    session.ai_agent_started_at = session.ai_agent_started_at or now
    session.updated_at = now
    db.flush()
    _write_event(
        db,
        session=session,
        event_type="voice.controller.dispatched",
        payload={
            "voice_session_id": session.public_id,
            "role": normalized_role,
            "provider_reference": provider_reference or None,
            "agent_name": normalized_agent,
        },
    )
    return leg


def ensure_recording_command(
    db: Session,
    *,
    session: WebchatVoiceSession,
    actor: User | None = None,
) -> None:
    if session.recording_status != "requested":
        return
    command = enqueue_voice_command(
        db,
        session=session,
        actor=actor,
        action_type="recording_start",
        note="voice_recording_policy",
        idempotency_key=f"voice-recording-start:{session.id}",
    )
    _write_event(
        db,
        session=session,
        event_type="voice.recording.requested",
        payload={
            "voice_session_id": session.public_id,
            "command_id": command.public_id,
            "actor_type": "operator" if actor is not None else "system",
        },
    )
