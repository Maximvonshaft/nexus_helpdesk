from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..utils.time import utc_now
from ..voice_models import WebchatVoiceSession, WebchatVoiceTranscriptSegment
from ..webchat_models import WebchatAITurn, WebchatConversation, WebchatMessage
from .customer_language import resolve_conversation_language
from .webchat_ai_orchestration_service import process_webchat_ai_reply_job
from .webchat_ai_service import AI_AUTHOR_LABEL
from .webchat_service import add_visitor_message_to_conversation

ACTIVE_AGENT_MODES = {"browser_ai", "sip_ai"}
ACTIVE_AGENT_STATUSES = {"ringing", "active"}


def _session_context(
    db: Session,
    *,
    conversation_public_id: str,
    voice_session_public_id: str,
) -> tuple[WebchatConversation, WebchatVoiceSession]:
    conversation = (
        db.query(WebchatConversation)
        .filter(WebchatConversation.public_id == conversation_public_id)
        .first()
    )
    session = (
        db.query(WebchatVoiceSession)
        .filter(WebchatVoiceSession.public_id == voice_session_public_id)
        .first()
    )
    if (
        conversation is None
        or session is None
        or session.conversation_id != conversation.id
        or session.provider != "livekit"
        or session.mode not in ACTIVE_AGENT_MODES
        or session.status not in ACTIVE_AGENT_STATUSES
        or session.ended_at is not None
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid LiveKit Agent session")
    return conversation, session


def _record_segment(
    db: Session,
    *,
    session: WebchatVoiceSession,
    segment_id: str,
    speaker_type: str,
    participant_identity: str,
    text: str,
    language: str | None,
) -> None:
    if session.transcript_status != "active":
        return
    exists = (
        db.query(WebchatVoiceTranscriptSegment.id)
        .filter(
            WebchatVoiceTranscriptSegment.voice_session_id == session.id,
            WebchatVoiceTranscriptSegment.segment_id == segment_id,
            WebchatVoiceTranscriptSegment.participant_identity == participant_identity,
        )
        .first()
    )
    if exists:
        return
    db.add(
        WebchatVoiceTranscriptSegment(
            voice_session_id=session.id,
            conversation_id=session.conversation_id,
            ticket_id=session.ticket_id,
            provider="livekit",
            provider_session_id=session.provider_room_name,
            provider_item_id=segment_id,
            participant_identity=participant_identity,
            speaker_type=speaker_type,
            speaker_label="Customer" if speaker_type == "visitor" else AI_AUTHOR_LABEL,
            segment_id=segment_id,
            language=language,
            is_final=True,
            text_raw=text,
            text_redacted=text,
            redaction_status="not_required",
            created_at=utc_now(),
        )
    )
    db.flush()


def process_livekit_agent_turn(
    db: Session,
    *,
    conversation_public_id: str,
    voice_session_public_id: str,
    turn_id: int,
    transcript: str,
    stt_language: str | None,
    participant_identity: str | None,
) -> dict[str, Any]:
    conversation, voice_session = _session_context(
        db,
        conversation_public_id=conversation_public_id,
        voice_session_public_id=voice_session_public_id,
    )
    client_message_id = f"livekit-voice:{voice_session.public_id}:{turn_id}"
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
        return {
            "ok": bool(existing_reply),
            "reply": (existing_reply.body_text or existing_reply.body) if existing_reply else None,
            "idempotent": True,
            "handoff_requested": conversation.handoff_status in {"requested", "accepted"},
        }

    message_result = add_visitor_message_to_conversation(
        db,
        conversation=conversation,
        body=transcript,
        client_message_id=client_message_id,
        message_type="voice_transcript",
        origin="livekit_agent",
    )
    visitor_message = db.get(WebchatMessage, int(message_result["message"]["id"]))
    if visitor_message is None:
        raise RuntimeError("LiveKit Agent visitor message was not persisted")
    _record_segment(
        db,
        session=voice_session,
        segment_id=f"{turn_id}:visitor",
        speaker_type="visitor",
        participant_identity=participant_identity or voice_session.provider_call_id or "customer",
        text=transcript,
        language=stt_language,
    )

    now = utc_now()
    ai_turn = WebchatAITurn(
        conversation_id=conversation.id,
        ticket_id=conversation.ticket_id,
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
        ticket_id=conversation.ticket_id,
        visitor_message_id=visitor_message.id,
    )
    reply = db.get(WebchatMessage, int(result["message_id"])) if result.get("message_id") else None
    history = (
        db.query(WebchatMessage.body)
        .filter(
            WebchatMessage.conversation_id == conversation.id,
            WebchatMessage.direction == "visitor",
        )
        .order_by(WebchatMessage.id.desc())
        .limit(6)
        .all()
    )
    language = resolve_conversation_language(
        transcript,
        previous_customer_messages=[row[0] for row in reversed(history) if row[0] != transcript],
    ).language
    if reply is not None:
        reply.message_type = "voice_transcript"
        _record_segment(
            db,
            session=voice_session,
            segment_id=f"{turn_id}:ai",
            speaker_type="ai",
            participant_identity="nexus-livekit-agent",
            text=reply.body_text or reply.body,
            language=language,
        )
    voice_session.status = "active"
    voice_session.active_at = voice_session.active_at or now
    voice_session.ai_agent_status = "active"
    voice_session.ai_turn_count = int(voice_session.ai_turn_count or 0) + 1
    voice_session.ai_language = language
    voice_session.updated_at = utc_now()
    db.flush()
    return {
        "ok": bool(reply),
        "reply": (reply.body_text or reply.body) if reply else None,
        "reply_message_id": reply.id if reply else None,
        "reply_source": result.get("reply_source"),
        "language": language,
        "status": result.get("status") or ("done" if reply else "null_reply"),
        "handoff_requested": conversation.handoff_status in {"requested", "accepted"},
        "active_agent_id": conversation.active_agent_id,
        "idempotent": False,
    }
