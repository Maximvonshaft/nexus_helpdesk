from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..models import Ticket
from ..utils.time import ensure_utc, utc_now
from ..voice_models import WebchatVoiceParticipant, WebchatVoiceSession, WebchatVoiceTranscriptSegment
from ..webchat_models import WebchatAITurn, WebchatConversation, WebchatMessage
from .webchat_ai_orchestration_service import process_webchat_ai_reply_job
from .webchat_ai_service import AI_AUTHOR_LABEL
from .webchat_ai_turn_service import safe_write_webchat_event
from .customer_language import resolve_conversation_language
from .webchat_service import add_visitor_message_to_conversation, get_authorized_webchat_conversation

VOICE_PROVIDER = "nexus_media_edge"
VOICE_MODE = "runtime_ai_voice"
ACTIVE_VOICE_STATUSES = {"active"}


def create_runtime_voice_session(
    db: Session,
    *,
    conversation_public_id: str,
    visitor_token: str | None,
    locale: str | None,
    ttl_seconds: int,
) -> WebchatVoiceSession:
    conversation = get_authorized_webchat_conversation(
        db,
        public_id=conversation_public_id,
        visitor_token=visitor_token,
    )
    conversation = (
        db.query(WebchatConversation)
        .filter(WebchatConversation.id == conversation.id)
        .with_for_update()
        .one()
    )
    if conversation.ticket_id is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="webchat conversation has no ticket")

    now = utc_now()
    active_sessions = (
        db.query(WebchatVoiceSession)
        .filter(
            WebchatVoiceSession.conversation_id == conversation.id,
            WebchatVoiceSession.provider == VOICE_PROVIDER,
            WebchatVoiceSession.mode == VOICE_MODE,
            WebchatVoiceSession.status.in_(ACTIVE_VOICE_STATUSES),
            WebchatVoiceSession.ended_at.is_(None),
        )
        .all()
    )
    for active_session in active_sessions:
        end_runtime_voice_session(db, voice_session_public_id=active_session.public_id, reason="replaced")

    public_id = f"voice_{secrets.token_urlsafe(18)}"
    session = WebchatVoiceSession(
        public_id=public_id,
        conversation_id=conversation.id,
        ticket_id=conversation.ticket_id,
        provider=VOICE_PROVIDER,
        provider_room_name=public_id,
        status="active",
        mode=VOICE_MODE,
        locale=(locale or None),
        recording_consent=False,
        recording_status="disabled",
        transcript_status="active",
        summary_status="pending",
        ai_agent_status="active",
        ai_agent_started_at=now,
        ai_language=(locale or None),
        started_at=now,
        active_at=now,
        expires_at=now + timedelta(seconds=max(60, ttl_seconds)),
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.flush()
    db.add(
        WebchatVoiceParticipant(
            voice_session_id=session.id,
            participant_type="visitor",
            visitor_label=conversation.visitor_name or "Visitor",
            provider_identity=f"visitor:{conversation.public_id}",
            status="joined",
            joined_at=now,
            created_at=now,
        )
    )
    safe_write_webchat_event(
        db,
        conversation_id=conversation.id,
        ticket_id=conversation.ticket_id,
        event_type="voice.session.active",
        payload={"voice_session_id": session.public_id, "provider": VOICE_PROVIDER, "mode": VOICE_MODE},
    )
    db.flush()
    return session


def authorize_runtime_voice_socket(
    db: Session,
    *,
    conversation_public_id: str,
    voice_session_public_id: str,
) -> tuple[WebchatConversation, WebchatVoiceSession]:
    conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == conversation_public_id).first()
    session = db.query(WebchatVoiceSession).filter(WebchatVoiceSession.public_id == voice_session_public_id).first()
    if (
        conversation is None
        or session is None
        or session.conversation_id != conversation.id
        or session.mode != VOICE_MODE
        or session.provider != VOICE_PROVIDER
        or session.status not in ACTIVE_VOICE_STATUSES
        or session.ended_at is not None
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid live voice session")
    expires_at = ensure_utc(session.expires_at)
    if expires_at is not None and expires_at <= utc_now():
        end_runtime_voice_session(db, voice_session_public_id=session.public_id, reason="expired")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="live voice session expired")
    return conversation, session


def end_runtime_voice_session(db: Session, *, voice_session_public_id: str, reason: str) -> None:
    session = db.query(WebchatVoiceSession).filter(WebchatVoiceSession.public_id == voice_session_public_id).first()
    if session is None or session.mode != VOICE_MODE or session.ended_at is not None:
        return
    now = utc_now()
    session.status = "ended"
    session.transcript_status = "completed"
    session.ai_agent_status = "ended"
    session.ai_agent_ended_at = now
    session.ended_at = now
    session.updated_at = now
    participants = (
        db.query(WebchatVoiceParticipant)
        .filter(
            WebchatVoiceParticipant.voice_session_id == session.id,
            WebchatVoiceParticipant.status.in_({"invited", "joined", "active"}),
        )
        .all()
    )
    for participant in participants:
        participant.status = "left"
        participant.left_at = participant.left_at or now
    safe_write_webchat_event(
        db,
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        event_type="voice.session.ended",
        payload={"voice_session_id": session.public_id, "reason": reason[:80]},
    )
    db.flush()


def process_runtime_voice_turn(
    db: Session,
    *,
    conversation_public_id: str,
    voice_session_public_id: str,
    turn_id: int,
    transcript: str,
    stt_language: str | None,
) -> dict[str, Any]:
    conversation, voice_session = authorize_runtime_voice_socket(
        db,
        conversation_public_id=conversation_public_id,
        voice_session_public_id=voice_session_public_id,
    )
    ticket = db.query(Ticket).filter(Ticket.id == conversation.ticket_id).first()
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ticket not found")

    client_message_id = f"live-voice:{voice_session.public_id}:{turn_id}"
    existing_visitor = (
        db.query(WebchatMessage)
        .filter(
            WebchatMessage.conversation_id == conversation.id,
            WebchatMessage.client_message_id == client_message_id,
            WebchatMessage.direction == "visitor",
        )
        .first()
    )
    if existing_visitor is not None:
        existing_reply = (
            db.query(WebchatMessage)
            .filter(
                WebchatMessage.conversation_id == conversation.id,
                WebchatMessage.direction == "agent",
                WebchatMessage.id > existing_visitor.id,
                WebchatMessage.author_label == AI_AUTHOR_LABEL,
            )
            .order_by(WebchatMessage.id.asc())
            .first()
        )
        return _turn_response(existing_reply, voice_session=voice_session, idempotent=True)

    message_result = add_visitor_message_to_conversation(
        db,
        conversation=conversation,
        body=transcript,
        client_message_id=client_message_id,
        message_type="voice_transcript",
        origin="live_voice",
    )
    visitor_message_id = int(message_result["message"]["id"])
    visitor_message = db.query(WebchatMessage).filter(WebchatMessage.id == visitor_message_id).one()
    _record_transcript_segment(
        db,
        session=voice_session,
        turn_id=turn_id,
        speaker_type="visitor",
        participant_identity=f"visitor:{conversation.public_id}",
        text=transcript,
        language=stt_language,
    )

    now = utc_now()
    ai_turn = WebchatAITurn(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        trigger_message_id=visitor_message.id,
        latest_visitor_message_id=visitor_message.id,
        context_cutoff_message_id=visitor_message.id,
        status="queued",
        is_public_reply_allowed=True,
        created_at=now,
        updated_at=now,
    )
    db.add(ai_turn)
    db.flush()
    conversation.active_ai_turn_id = ai_turn.id
    conversation.active_ai_status = "queued"
    conversation.active_ai_for_message_id = visitor_message.id
    conversation.active_ai_context_cutoff_message_id = visitor_message.id
    conversation.active_ai_started_at = now
    conversation.active_ai_updated_at = now
    db.flush()

    result = process_webchat_ai_reply_job(
        db,
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        visitor_message_id=visitor_message.id,
    )
    reply = None
    if result.get("message_id"):
        reply = db.query(WebchatMessage).filter(WebchatMessage.id == int(result["message_id"])).first()
    if reply is not None:
        reply.message_type = "voice_transcript"
        _record_transcript_segment(
            db,
            session=voice_session,
            turn_id=turn_id,
            speaker_type="ai",
            participant_identity="provider_runtime",
            text=reply.body_text or reply.body,
            language=_reply_language(transcript, conversation),
        )

    voice_session.ai_turn_count = int(voice_session.ai_turn_count or 0) + 1
    voice_session.ai_language = _reply_language(transcript, conversation)
    voice_session.updated_at = utc_now()
    db.flush()
    return _turn_response(reply, voice_session=voice_session, result=result)


def _record_transcript_segment(
    db: Session,
    *,
    session: WebchatVoiceSession,
    turn_id: int,
    speaker_type: str,
    participant_identity: str,
    text: str,
    language: str | None,
) -> None:
    segment_id = f"{turn_id}:{speaker_type}"
    existing = (
        db.query(WebchatVoiceTranscriptSegment.id)
        .filter(
            WebchatVoiceTranscriptSegment.provider == VOICE_PROVIDER,
            WebchatVoiceTranscriptSegment.provider_session_id == session.public_id,
            WebchatVoiceTranscriptSegment.segment_id == segment_id,
            WebchatVoiceTranscriptSegment.participant_identity == participant_identity,
        )
        .first()
    )
    if existing:
        return
    db.add(
        WebchatVoiceTranscriptSegment(
            voice_session_id=session.id,
            conversation_id=session.conversation_id,
            ticket_id=session.ticket_id,
            provider=VOICE_PROVIDER,
            provider_session_id=session.public_id,
            provider_item_id=segment_id,
            participant_identity=participant_identity,
            speaker_type=speaker_type,
            speaker_label="Customer" if speaker_type == "visitor" else AI_AUTHOR_LABEL,
            segment_id=segment_id,
            language=(language or None),
            is_final=True,
            text_raw=text,
            text_redacted=text,
            redaction_status="not_required",
            created_at=utc_now(),
        )
    )
    db.flush()


def _reply_language(transcript: str, conversation: WebchatConversation) -> str:
    history = (
        Session.object_session(conversation)
        .query(WebchatMessage.body)
        .filter(WebchatMessage.conversation_id == conversation.id, WebchatMessage.direction == "visitor")
        .order_by(WebchatMessage.id.desc())
        .limit(6)
        .all()
    )
    previous = [row[0] for row in reversed(history) if row[0] != transcript]
    return resolve_conversation_language(transcript, previous_customer_messages=previous).language


def _turn_response(
    reply: WebchatMessage | None,
    *,
    voice_session: WebchatVoiceSession,
    result: dict[str, Any] | None = None,
    idempotent: bool = False,
) -> dict[str, Any]:
    result = result or {}
    runtime_trace = result.get("runtime_trace") if isinstance(result.get("runtime_trace"), dict) else {}
    return {
        "ok": bool(reply),
        "reply": (reply.body_text or reply.body) if reply is not None else None,
        "reply_message_id": reply.id if reply is not None else None,
        "reply_source": result.get("reply_source") or ("provider_runtime" if reply is not None else None),
        "runtime_trace_id": runtime_trace.get("runtime_trace_id") or runtime_trace.get("request_id"),
        "language": voice_session.ai_language,
        "status": result.get("status") or ("done" if reply is not None else "null_reply"),
        "idempotent": idempotent,
    }
