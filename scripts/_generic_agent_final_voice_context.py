from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text.rstrip() + "\n", encoding="utf-8")


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def function_bounds(text: str, name: str) -> tuple[int, int]:
    match = re.search(rf"^def {re.escape(name)}\(", text, flags=re.MULTILINE)
    if match is None:
        raise SystemExit(f"function not found: {name}")
    next_match = re.search(r"^def [A-Za-z0-9_]+\(", text[match.end():], flags=re.MULTILINE)
    end = len(text) if next_match is None else match.end() + next_match.start()
    return match.start(), end


def replace_function(text: str, name: str, replacement: str) -> str:
    start, end = function_bounds(text, name)
    return text[:start].rstrip() + "\n\n\n" + replacement.strip() + "\n\n\n" + text[end:].lstrip("\n")


def remove_function(text: str, name: str) -> str:
    start, end = function_bounds(text, name)
    return text[:start].rstrip() + "\n\n\n" + text[end:].lstrip("\n")


voice_service_path = "backend/app/services/webchat_voice_service.py"
voice = read(voice_service_path)
voice = remove_function(voice, "_ensure_ticket_visible_for_session")
helper_marker = "def _issue_token("
helper_pos = voice.find(helper_marker)
if helper_pos < 0:
    raise SystemExit("voice helper insertion marker missing")
helpers = '''def _load_voice_session_context(
    db: Session,
    voice_session_public_id: str,
) -> tuple[WebchatVoiceSession, WebchatConversation]:
    session = _load_voice_session(db, voice_session_public_id)
    conversation = db.get(WebchatConversation, session.conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="webchat conversation not found")
    return session, conversation


def _visible_voice_session_context(
    db: Session,
    *,
    voice_session_public_id: str,
    current_user: User,
) -> tuple[WebchatVoiceSession, WebchatConversation, Ticket | None]:
    session, conversation = _load_voice_session_context(db, voice_session_public_id)
    ticket = _ensure_voice_session_visible(db, current_user, session, conversation)
    return session, conversation, ticket


'''
voice = voice[:helper_pos] + helpers + voice[helper_pos:]

voice = replace_function(
    voice,
    "list_admin_voice_evidence",
    '''def list_admin_voice_evidence(
    db: Session,
    *,
    voice_session_public_id: str,
    current_user: User,
    limit: int = 50,
) -> dict[str, Any]:
    ensure_can_read_webcall_voice(current_user, db)
    session, _conversation, _ticket = _visible_voice_session_context(
        db,
        voice_session_public_id=voice_session_public_id,
        current_user=current_user,
    )
    safe_limit = max(1, min(int(limit or 50), 100))
    segments = (
        db.query(WebchatVoiceTranscriptSegment)
        .filter(WebchatVoiceTranscriptSegment.voice_session_id == session.id)
        .order_by(WebchatVoiceTranscriptSegment.start_ms.asc().nullslast(), WebchatVoiceTranscriptSegment.id.asc())
        .limit(safe_limit)
        .all()
    )
    turns = (
        db.query(WebchatVoiceAITurn)
        .filter(WebchatVoiceAITurn.voice_session_id == session.id)
        .order_by(WebchatVoiceAITurn.turn_index.asc(), WebchatVoiceAITurn.id.asc())
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
        "transcript_segments": [
            {
                "id": segment.id,
                "segment_id": segment.segment_id,
                "speaker_type": segment.speaker_type,
                "speaker_label": segment.speaker_label,
                "language": segment.language,
                "is_final": segment.is_final,
                "start_ms": segment.start_ms,
                "end_ms": segment.end_ms,
                "text": segment.text_redacted or "[redaction pending]",
                "confidence": segment.confidence,
                "redaction_status": segment.redaction_status,
                "created_at": _serialize_dt(segment.created_at),
            }
            for segment in segments
        ],
        "ai_turns": [
            {
                "id": turn.id,
                "turn_index": turn.turn_index,
                "customer_text_redacted": turn.customer_text_redacted,
                "ai_response_text_redacted": turn.ai_response_text_redacted,
                "language": turn.language,
                "intent": turn.intent,
                "action": turn.action,
                "handoff_required": turn.handoff_required,
                "handoff_reason": turn.handoff_reason,
                "confidence": turn.confidence,
                "provider": turn.provider,
                "stt_provider": turn.stt_provider,
                "tts_provider": turn.tts_provider,
                "latency_ms": turn.latency_ms,
                "created_at": _serialize_dt(turn.created_at),
            }
            for turn in turns
        ],
        "ai_actions": [
            {
                "id": action.id,
                "turn_id": action.turn_id,
                "model_action": action.model_action,
                "nexus_decision": action.nexus_decision,
                "decision_reason": action.decision_reason,
                "speedaf_tool_name": action.speedaf_tool_name,
                "background_job_id": action.background_job_id,
                "tool_call_log_id": action.tool_call_log_id,
                "result_status": action.result_status,
                "created_at": _serialize_dt(action.created_at),
            }
            for action in actions
        ],
    }''',
)

voice = replace_function(
    voice,
    "list_admin_voice_actions",
    '''def list_admin_voice_actions(
    db: Session,
    *,
    voice_session_public_id: str,
    current_user: User,
    limit: int = 20,
) -> dict[str, Any]:
    ensure_can_read_webcall_voice(current_user, db)
    session, _conversation, _ticket = _visible_voice_session_context(
        db,
        voice_session_public_id=voice_session_public_id,
        current_user=current_user,
    )
    safe_limit = max(1, min(int(limit or 20), 50))
    actions = (
        db.query(WebchatVoiceSessionAction)
        .filter(
            WebchatVoiceSessionAction.voice_session_id == session.id,
            WebchatVoiceSessionAction.action_type != "note",
        )
        .order_by(WebchatVoiceSessionAction.id.desc())
        .limit(safe_limit)
        .all()
    )
    return {"items": [_serialize_session_action(action) for action in actions]}''',
)

write(voice_service_path, voice)
