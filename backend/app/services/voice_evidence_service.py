from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..enums import EventType
from ..models import TicketInternalNote, User
from ..utils.time import utc_now
from ..voice_compliance_models import VoiceComplianceEvidence
from ..voice_models import (
    VoiceChannelConfiguration,
    WebchatVoiceAIAction,
    WebchatVoiceAITurn,
    WebchatVoiceSessionAction,
    WebchatVoiceTranscriptSegment,
)
from ..webchat_models import WebchatEvent
from .audit_service import log_admin_audit, log_event
from .permissions import (
    ensure_can_control_webcall_voice,
    ensure_can_read_webcall_voice,
    ensure_can_write_internal_note,
)
from .voice_command_service import enqueue_voice_command, serialize_voice_command
from .voice_compliance_service import capability_authorized, evidence_projection
from .voice_session_service import TERMINAL_STATUSES, _visible_context

CALL_CONTROL_ACTIVE_STATUSES = {"accepted", "active"}


def _serialize_dt(value) -> str | None:
    return value.isoformat() if value else None


def list_admin_voice_evidence(
    db: Session,
    *,
    voice_session_public_id: str,
    current_user: User,
    limit: int = 50,
) -> dict[str, Any]:
    ensure_can_read_webcall_voice(current_user, db)
    session, _conversation, _ticket = _visible_context(
        db,
        public_id=voice_session_public_id,
        current_user=current_user,
    )
    safe_limit = max(1, min(int(limit or 50), 100))
    compliance_rows = (
        db.query(VoiceComplianceEvidence)
        .filter(VoiceComplianceEvidence.voice_session_id == session.id)
        .order_by(
            VoiceComplianceEvidence.evidence_at.asc(),
            VoiceComplianceEvidence.id.asc(),
        )
        .limit(safe_limit)
        .all()
    )
    segments = (
        db.query(WebchatVoiceTranscriptSegment)
        .filter(
            WebchatVoiceTranscriptSegment.voice_session_id == session.id
        )
        .order_by(
            WebchatVoiceTranscriptSegment.start_ms.asc().nullslast(),
            WebchatVoiceTranscriptSegment.id.asc(),
        )
        .limit(safe_limit)
        .all()
    )
    turns = (
        db.query(WebchatVoiceAITurn)
        .filter(WebchatVoiceAITurn.voice_session_id == session.id)
        .order_by(
            WebchatVoiceAITurn.turn_index.asc(),
            WebchatVoiceAITurn.id.asc(),
        )
        .limit(safe_limit)
        .all()
    )
    actions = (
        db.query(WebchatVoiceAIAction)
        .filter(WebchatVoiceAIAction.voice_session_id == session.id)
        .order_by(WebchatVoiceAIAction.id.asc())
        .limit(safe_limit)
        .all()
    )
    return {
        "ok": True,
        "ticket_id": session.ticket_id,
        "voice_session_id": session.public_id,
        "status": session.status,
        "provider": session.provider,
        "recording_status": session.recording_status,
        "transcript_status": session.transcript_status,
        "summary_status": session.summary_status,
        "ai_agent_status": session.ai_agent_status,
        "ai_turn_count": session.ai_turn_count,
        "compliance_evidence": [
            evidence_projection(row)
            for row in compliance_rows
        ],
        "transcript_segments": [
            {
                "id": row.id,
                "segment_id": row.segment_id,
                "speaker_type": row.speaker_type,
                "speaker_label": row.speaker_label,
                "language": row.language,
                "is_final": row.is_final,
                "start_ms": row.start_ms,
                "end_ms": row.end_ms,
                "text": row.text_redacted or row.text_raw,
                "confidence": row.confidence,
                "redaction_status": row.redaction_status,
                "created_at": _serialize_dt(row.created_at),
            }
            for row in segments
        ],
        "ai_turns": [
            {
                "id": row.id,
                "turn_index": row.turn_index,
                "customer_text_redacted": row.customer_text_redacted,
                "ai_response_text_redacted": row.ai_response_text_redacted,
                "language": row.language,
                "intent": row.intent,
                "action": row.action,
                "handoff_required": row.handoff_required,
                "handoff_reason": row.handoff_reason,
                "confidence": row.confidence,
                "provider": row.provider,
                "stt_provider": row.stt_provider,
                "tts_provider": row.tts_provider,
                "latency_ms": row.latency_ms,
                "created_at": _serialize_dt(row.created_at),
            }
            for row in turns
        ],
        "ai_actions": [
            {
                "id": row.id,
                "turn_id": row.turn_id,
                "model_action": row.model_action,
                "nexus_decision": row.nexus_decision,
                "decision_reason": row.decision_reason,
                "speedaf_tool_name": row.speedaf_tool_name,
                "background_job_id": row.background_job_id,
                "tool_call_log_id": row.tool_call_log_id,
                "result_status": row.result_status,
                "created_at": _serialize_dt(row.created_at),
            }
            for row in actions
        ],
    }


def list_admin_voice_actions(
    db: Session,
    *,
    voice_session_public_id: str,
    current_user: User,
    limit: int = 20,
) -> dict[str, Any]:
    ensure_can_read_webcall_voice(current_user, db)
    session, _conversation, _ticket = _visible_context(
        db,
        public_id=voice_session_public_id,
        current_user=current_user,
    )
    rows = (
        db.query(WebchatVoiceSessionAction)
        .filter(WebchatVoiceSessionAction.voice_session_id == session.id)
        .order_by(WebchatVoiceSessionAction.id.desc())
        .limit(max(1, min(int(limit or 20), 100)))
        .all()
    )
    return {"items": [serialize_voice_command(row) for row in rows]}


def record_admin_voice_action(
    db: Session,
    *,
    voice_session_public_id: str,
    current_user: User,
    action_type: str,
    target: str | None = None,
    digits: str | None = None,
    note: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    ensure_can_control_webcall_voice(current_user, db)
    session, _conversation, _ticket = _visible_context(
        db,
        public_id=voice_session_public_id,
        current_user=current_user,
    )
    requested = str(action_type or "").strip().lower()
    if session.status in TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="voice session already closed",
        )
    if requested not in {
        "hangup",
        "recording_start",
        "recording_stop",
    } and session.status not in CALL_CONTROL_ACTIVE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="voice session action requires an active call",
        )
    if requested == "recording_start":
        configuration = (
            db.query(VoiceChannelConfiguration)
            .filter(
                VoiceChannelConfiguration.channel_account_id
                == session.channel_account_id
            )
            .first()
            if session.channel_account_id is not None
            else None
        )
        policy = (
            configuration.recording_policy
            if configuration is not None
            else "disabled"
        )
        if not capability_authorized(
            db,
            session=session,
            capability="recording",
            policy=policy,
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="voice recording compliance evidence is required",
            )
    command = enqueue_voice_command(
        db,
        session=session,
        actor=current_user,
        action_type=requested,
        target=target,
        digits=digits,
        note=note,
        idempotency_key=idempotency_key,
    )
    session.updated_at = utc_now()
    db.flush()
    return {
        "ok": True,
        "ticket_id": session.ticket_id,
        "voice_session_id": session.public_id,
        "action": serialize_voice_command(command),
    }


def save_admin_voice_note(
    db: Session,
    *,
    voice_session_public_id: str,
    current_user: User,
    body: str,
    source: str | None = None,
) -> dict[str, Any]:
    ensure_can_read_webcall_voice(current_user, db)
    ensure_can_write_internal_note(current_user, db)
    session, _conversation, ticket = _visible_context(
        db,
        public_id=voice_session_public_id,
        current_user=current_user,
    )
    normalized_body = str(body or "").strip()
    if not normalized_body:
        raise HTTPException(
            status_code=422,
            detail="voice note body is required",
        )
    source_value = str(
        source or "voice_operator_workbench"
    ).strip()[:80]
    now = utc_now()
    ticket_note = None
    ticket_event = None
    if ticket is not None:
        ticket_note = TicketInternalNote(
            ticket_id=ticket.id,
            author_id=current_user.id,
            body=normalized_body,
            created_at=now,
            updated_at=now,
        )
        db.add(ticket_note)
        db.flush()
        ticket_event = log_event(
            db,
            ticket_id=ticket.id,
            actor_id=current_user.id,
            event_type=EventType.internal_note_added,
            note="Voice call note saved",
            payload={
                "voice_session_id": session.public_id,
                "note_id": ticket_note.id,
                "source": source_value,
            },
        )
    webchat_event = WebchatEvent(
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        event_type="voice.session.note_saved",
        payload_json=json.dumps(
            {
                "voice_session_id": session.public_id,
                "ticket_note_id": (
                    ticket_note.id if ticket_note is not None else None
                ),
                "source": source_value,
                "author_id": current_user.id,
                "body_length": len(normalized_body),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        created_at=now,
    )
    db.add(webchat_event)
    db.flush()
    audit = log_admin_audit(
        db,
        actor_id=current_user.id,
        action="webcall.voice.note_saved",
        target_type="webchat_voice_session",
        target_id=session.id,
        old_value=None,
        new_value={
            "voice_session_id": session.public_id,
            "ticket_id": session.ticket_id,
            "source": source_value,
            "body_length": len(normalized_body),
        },
    )
    note_id = (
        ticket_note.id if ticket_note is not None else webchat_event.id
    )
    return {
        "ok": True,
        "ticket_id": session.ticket_id,
        "voice_session_id": session.public_id,
        "note_id": note_id,
        "ticket_event_id": (
            ticket_event.id if ticket_event is not None else None
        ),
        "webchat_event_id": webchat_event.id,
        "audit_id": audit.id,
        "created_at": now.isoformat(),
    }
