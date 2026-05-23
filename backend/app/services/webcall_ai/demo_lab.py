from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ...enums import ConversationState, SourceChannel, TicketPriority, TicketSource, TicketStatus
from ...models import Ticket, User
from ...utils.time import ensure_utc, format_utc, utc_now
from ...voice_models import WebchatVoiceAIAction, WebchatVoiceAITurn, WebchatVoiceSession, WebchatVoiceTranscriptSegment
from ...webchat_models import WebchatConversation, WebchatEvent
from ...webchat_voice_config import load_webchat_voice_runtime_config
from .config import get_webcall_ai_settings
from .demo_config import WebCallAIDemoLabSettings, get_webcall_ai_demo_lab_settings

logger = logging.getLogger(__name__)

DEMO_MODE = "internal_ai_demo"
DEMO_PROVIDER = "demo_lab"
DEMO_TENANT = "internal-demo"
TERMINAL_STATUSES = {"ended", "missed", "failed", "cancelled", "canceled", "expired", "rejected"}
UNSAFE_ACTION_TERMS = {"cancel", "cancellation", "address change", "change address", "driver phone", "work order"}
TRACKING_RE = re.compile(r"\b[A-Z]{1,4}\d{6,}[A-Z]{0,3}\b", re.IGNORECASE)


@dataclass(frozen=True)
class DemoDecision:
    intent: str
    action: str
    reply: str
    handoff_required: bool
    confidence: int


def _error(status_code: int, error_code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"ok": False, "error_code": error_code, "message": message})


def _safe_text(value: str | None, *, max_chars: int) -> str:
    text = (value or "").strip()
    text = re.sub(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", "[redacted_email]", text)
    text = re.sub(r"\+?\d[\d\s().-]{7,}\d", "[redacted_phone]", text)
    text = re.sub(r"\s+", " ", text)
    return text[:max_chars]


def _new_public_id() -> str:
    return f"wv_demo_{secrets.token_urlsafe(18).replace('-', '').replace('_', '')[:24]}"


def _ticket_no(public_id: str) -> str:
    digest = hashlib.sha256(public_id.encode("utf-8")).hexdigest()[:12].upper()
    return f"DEMO-{digest}"


def _serialize_session(session: WebchatVoiceSession) -> dict[str, Any]:
    return {
        "public_id": session.public_id,
        "mode": session.mode,
        "status": session.status,
        "locale": session.locale,
        "recording_status": session.recording_status,
        "transcript_status": session.transcript_status,
        "summary_status": session.summary_status,
        "ai_agent_status": session.ai_agent_status,
        "ai_turn_count": session.ai_turn_count,
        "created_at": format_utc(session.created_at),
        "ended_at": format_utc(session.ended_at),
    }


def _event_payload(event_type: str, summary: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"type": event_type, "demo": True}
    if summary:
        payload["summary"] = summary[:240]
    return payload


def _write_event(db: Session, session: WebchatVoiceSession, event_type: str, *, summary: str | None = None) -> WebchatEvent:
    event = WebchatEvent(
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        event_type=f"webcall_ai_demo.{event_type}",
        payload_json=json.dumps(_event_payload(event_type, summary), ensure_ascii=False),
        created_at=utc_now(),
    )
    db.add(event)
    return event


def _active_demo_sessions(db: Session) -> int:
    return int(
        db.query(WebchatVoiceSession)
        .filter(
            WebchatVoiceSession.mode == DEMO_MODE,
            WebchatVoiceSession.status == "active",
            WebchatVoiceSession.ended_at.is_(None),
        )
        .count()
    )


def get_demo_lab_status(db: Session | None = None, current_user: User | None = None) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    try:
        settings = get_webcall_ai_demo_lab_settings()
        config_error = None
    except RuntimeError as exc:
        settings = WebCallAIDemoLabSettings(False, True, "simulated_full_loop", True, False, (), 3, 8, 1000, 200)
        config_error = str(exc)
        blockers.append(config_error)
    try:
        voice_config = load_webchat_voice_runtime_config()
        public_customer_entry_enabled = bool(voice_config.enabled)
        recording_enabled = bool(voice_config.recording_enabled)
        transcription_enabled = bool(voice_config.transcription_enabled)
    except RuntimeError as exc:
        public_customer_entry_enabled = False
        recording_enabled = False
        transcription_enabled = False
        warnings.append(f"webchat_voice_runtime_unavailable:{type(exc).__name__}")
    try:
        ai_settings = get_webcall_ai_settings()
        ai_agent_enabled = bool(ai_settings.enabled)
    except RuntimeError as exc:
        ai_agent_enabled = False
        warnings.append(f"webcall_ai_runtime_unavailable:{type(exc).__name__}")

    try:
        active_demo_sessions = _active_demo_sessions(db) if db is not None else 0
    except (AttributeError, TypeError, SQLAlchemyError) as exc:
        active_demo_sessions = 0
        warnings.append(f"webcall_ai_demo_lab status unavailable: {type(exc).__name__}")
    if not settings.demo_lab_enabled:
        status_value = "disabled"
    elif settings.demo_lab_kill_switch:
        status_value = "blocked"
        blockers.append("kill_switch_enabled")
    elif config_error:
        status_value = "blocked"
    else:
        status_value = "ready"

    return {
        "ok": True,
        "status": status_value,
        "enabled": settings.demo_lab_enabled,
        "kill_switch": settings.demo_lab_kill_switch,
        "internal_only": True,
        "public_customer_entry_enabled": public_customer_entry_enabled,
        "recording_enabled": recording_enabled,
        "transcription_enabled": transcription_enabled,
        "ai_agent_enabled": ai_agent_enabled,
        "demo_mode": settings.demo_lab_mode,
        "allow_browser_speech": settings.demo_lab_allow_browser_speech,
        "allow_real_media": settings.demo_lab_allow_real_media,
        "active_demo_sessions": active_demo_sessions,
        "max_active_sessions": settings.demo_lab_max_active_sessions,
        "max_turns_per_session": settings.demo_lab_max_turns_per_session,
        "blockers": blockers,
        "warnings": warnings,
    }


def _require_ready(db: Session) -> WebCallAIDemoLabSettings:
    settings = get_webcall_ai_demo_lab_settings()
    if not settings.demo_lab_enabled:
        raise _error(status.HTTP_404_NOT_FOUND, "demo_lab_disabled", "WebCall AI demo lab is disabled")
    if settings.demo_lab_kill_switch:
        raise _error(status.HTTP_423_LOCKED, "demo_lab_kill_switch", "WebCall AI demo lab kill switch is enabled")
    return settings


def create_demo_session(db: Session, current_user: User, payload: Any) -> dict[str, Any]:
    settings = _require_ready(db)
    if _active_demo_sessions(db) >= settings.demo_lab_max_active_sessions:
        raise _error(status.HTTP_409_CONFLICT, "demo_lab_quota_exceeded", "Maximum active demo sessions reached")

    now = utc_now()
    public_id = _new_public_id()
    locale = _safe_text(getattr(payload, "locale", None) or "en", max_chars=20)
    display_name = _safe_text(getattr(payload, "display_name", None) or "Internal Demo", max_chars=80)
    scenario = _safe_text(getattr(payload, "scenario", None) or "tracking_question", max_chars=80)
    ticket = Ticket(
        ticket_no=_ticket_no(public_id),
        title=f"Internal WebCall AI Demo - {scenario}",
        description="Internal WebCall AI demo sandbox session. No customer-facing outbound action.",
        source=TicketSource.manual,
        source_channel=SourceChannel.internal,
        priority=TicketPriority.low,
        status=TicketStatus.in_progress,
        conversation_state=ConversationState.ai_active,
        created_by=current_user.id,
        created_at=now,
        updated_at=now,
    )
    db.add(ticket)
    db.flush()
    conversation = WebchatConversation(
        public_id=f"wc_demo_{public_id[8:]}",
        visitor_token_hash=hashlib.sha256(public_id.encode("utf-8")).hexdigest(),
        visitor_token_expires_at=now + timedelta(hours=2),
        tenant_key=DEMO_TENANT,
        channel_key="webcall_ai_demo",
        ticket_id=ticket.id,
        visitor_name=display_name,
        origin="internal_demo",
        page_url="/webcall-ai-demo",
        status="open",
        created_at=now,
        updated_at=now,
        last_seen_at=now,
    )
    db.add(conversation)
    db.flush()
    session = WebchatVoiceSession(
        public_id=public_id,
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        provider=DEMO_PROVIDER,
        provider_room_name=f"demo_{public_id}",
        status="active",
        mode=DEMO_MODE,
        locale=locale,
        recording_consent=False,
        recording_status="disabled",
        transcript_status="demo_text_only",
        summary_status="disabled",
        ai_agent_status="ready",
        ai_turn_count=0,
        accepted_by_user_id=current_user.id,
        started_at=now,
        accepted_at=now,
        active_at=now,
        expires_at=now + timedelta(hours=2),
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.flush()
    event = _write_event(db, session, "demo_session_created", summary=f"Scenario: {scenario}")
    db.flush()
    logger.info("webcall_ai_demo_session_created", extra={"voice_session_public_id": public_id, "actor_user_id": current_user.id, "status": session.status})
    return {"ok": True, "session": _serialize_session(session), "events": [_serialize_event(event)]}


def _load_demo_session(db: Session, current_user: User, public_id: str) -> WebchatVoiceSession:
    session = db.query(WebchatVoiceSession).filter(WebchatVoiceSession.public_id == public_id).first()
    if session is None or session.mode != DEMO_MODE:
        raise _error(status.HTTP_404_NOT_FOUND, "demo_session_not_found", "WebCall AI demo session not found")
    if session.accepted_by_user_id not in {None, current_user.id}:
        raise _error(status.HTTP_404_NOT_FOUND, "demo_session_not_found", "WebCall AI demo session not found")
    return session


def _decision(text: str) -> DemoDecision:
    lowered = text.lower()
    if any(term in lowered for term in UNSAFE_ACTION_TERMS):
        return DemoDecision(
            intent="handoff_required",
            action="handoff",
            reply="For address changes, cancellation, work orders, or driver phone requests, I need to transfer this to a human agent.",
            handoff_required=True,
            confidence=90,
        )
    if TRACKING_RE.search(text):
        return DemoDecision(
            intent="tracking",
            action="safe_reply",
            reply="I do not have verified parcel status in this demo mode. I can only show the safe demo flow and transfer to a human for real shipment checks.",
            handoff_required=False,
            confidence=82,
        )
    if "track" in lowered or "parcel" in lowered or "package" in lowered or "where" in lowered:
        return DemoDecision(
            intent="tracking",
            action="ask_tracking_number",
            reply="I can help check this. Please provide the tracking number.",
            handoff_required=False,
            confidence=80,
        )
    return DemoDecision(
        intent="demo_general",
        action="safe_reply",
        reply="This is the internal WebCall AI demo sandbox. I can demonstrate a safe text-only AI turn and browser speech playback fallback.",
        handoff_required=False,
        confidence=75,
    )


def process_demo_turn(db: Session, current_user: User, public_id: str, payload: Any) -> dict[str, Any]:
    started = time.monotonic()
    settings = _require_ready(db)
    session = _load_demo_session(db, current_user, public_id)
    if session.status in TERMINAL_STATUSES or session.ended_at is not None:
        raise _error(status.HTTP_409_CONFLICT, "demo_session_terminal", "Demo session is already closed")
    expires_at = ensure_utc(session.expires_at)
    if expires_at is not None and expires_at <= utc_now():
        session.status = "expired"
        session.ended_at = utc_now()
        session.updated_at = session.ended_at
        raise _error(status.HTTP_409_CONFLICT, "demo_session_expired", "Demo session expired")
    client_turn_id = _safe_text(getattr(payload, "client_turn_id", None), max_chars=120)
    if not client_turn_id:
        raise _error(status.HTTP_422_UNPROCESSABLE_ENTITY, "client_turn_id_required", "client_turn_id is required")
    input_mode = _safe_text(getattr(payload, "input_mode", None) or "typed", max_chars=40)
    if input_mode not in {"typed", "browser_speech", "fixture"}:
        raise _error(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_input_mode", "input_mode must be typed, browser_speech, or fixture")
    text = _safe_text(getattr(payload, "text", None), max_chars=settings.demo_lab_max_input_chars + 1)
    if not text:
        raise _error(status.HTTP_422_UNPROCESSABLE_ENTITY, "text_required", "text is required")
    if len(text) > settings.demo_lab_max_input_chars:
        raise _error(status.HTTP_422_UNPROCESSABLE_ENTITY, "text_too_long", "Demo input exceeds maximum length")

    segment_id = f"demo-turn:{client_turn_id}"
    existing_segment = (
        db.query(WebchatVoiceTranscriptSegment)
        .filter(
            WebchatVoiceTranscriptSegment.voice_session_id == session.id,
            WebchatVoiceTranscriptSegment.provider == DEMO_PROVIDER,
            WebchatVoiceTranscriptSegment.segment_id == segment_id,
            WebchatVoiceTranscriptSegment.participant_identity == "demo_operator",
        )
        .first()
    )
    if existing_segment is not None:
        if existing_segment.text_redacted != text:
            raise _error(status.HTTP_409_CONFLICT, "idempotency_conflict", "client_turn_id was already used with different text")
        turn = (
            db.query(WebchatVoiceAITurn)
            .filter(WebchatVoiceAITurn.voice_session_id == session.id, WebchatVoiceAITurn.turn_index == existing_segment.confidence)
            .first()
        )
        if turn is not None:
            return _turn_response(session, turn, existing_segment, [])

    if session.ai_turn_count >= settings.demo_lab_max_turns_per_session:
        raise _error(status.HTTP_409_CONFLICT, "demo_max_turns_reached", "Maximum demo turns reached")

    now = utc_now()
    turn_index = session.ai_turn_count + 1
    decision = _decision(text)
    segment = WebchatVoiceTranscriptSegment(
        voice_session_id=session.id,
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        provider=DEMO_PROVIDER,
        provider_session_id=session.public_id,
        provider_item_id=segment_id,
        participant_identity="demo_operator",
        speaker_type="demo_operator",
        speaker_label="Internal demo operator",
        segment_id=segment_id,
        language=_safe_text(getattr(payload, "locale", None) or session.locale or "en", max_chars=20),
        is_final=True,
        text_raw=text,
        text_redacted=text,
        confidence=turn_index,
        redaction_status="redacted",
        created_at=now,
    )
    db.add(segment)
    db.flush()
    turn = WebchatVoiceAITurn(
        voice_session_id=session.id,
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        turn_index=turn_index,
        customer_text_redacted=text,
        ai_response_text_redacted=decision.reply,
        language=segment.language,
        intent=decision.intent,
        action=decision.action,
        handoff_required=decision.handoff_required,
        handoff_reason="unsafe_or_external_action" if decision.handoff_required else None,
        confidence=decision.confidence,
        provider=DEMO_PROVIDER,
        stt_provider="browser_speech" if input_mode == "browser_speech" else "typed",
        tts_provider="browser_speech" if settings.demo_lab_allow_browser_speech else "text",
        latency_ms=int((time.monotonic() - started) * 1000),
        created_at=now,
    )
    db.add(turn)
    db.flush()
    action = WebchatVoiceAIAction(
        voice_session_id=session.id,
        turn_id=turn.id,
        model_action=decision.action,
        nexus_decision="handoff" if decision.handoff_required else "allowed",
        decision_reason="demo_lab_no_external_write",
        result_status="demo_only",
        created_at=now,
    )
    db.add(action)
    session.ai_turn_count = turn_index
    session.ai_agent_status = "ready"
    session.updated_at = now
    events = [
        _write_event(db, session, "listening", summary=input_mode),
        _write_event(db, session, "thinking"),
        _write_event(db, session, "handoff_required" if decision.handoff_required else "speaking"),
    ]
    db.flush()
    logger.info("webcall_ai_demo_turn_completed", extra={"voice_session_public_id": session.public_id, "actor_user_id": current_user.id, "status": "completed", "elapsed_ms": turn.latency_ms})
    return _turn_response(session, turn, segment, events)


def _turn_response(session: WebchatVoiceSession, turn: WebchatVoiceAITurn, segment: WebchatVoiceTranscriptSegment, events: list[WebchatEvent]) -> dict[str, Any]:
    return {
        "ok": True,
        "voice_session_public_id": session.public_id,
        "turn": {
            "id": turn.id,
            "turn_index": turn.turn_index,
            "status": "completed",
            "customer_text_redacted": turn.customer_text_redacted,
            "ai_response_text_redacted": turn.ai_response_text_redacted,
            "language": turn.language,
            "intent": turn.intent,
            "action": turn.action,
            "handoff_required": turn.handoff_required,
            "confidence": turn.confidence,
            "tts_mode": "browser_speech" if turn.tts_provider == "browser_speech" else "text",
            "created_at": format_utc(turn.created_at),
        },
        "evidence": {"voice_ai_turn_id": turn.id, "transcript_segment_id": segment.id, "tool_call_log_id": None},
        "events": [_serialize_event(event) for event in events],
    }


def end_demo_session(db: Session, current_user: User, public_id: str, payload: Any) -> dict[str, Any]:
    _require_ready(db)
    session = _load_demo_session(db, current_user, public_id)
    if session.status != "ended":
        now = utc_now()
        session.status = "ended"
        session.ai_agent_status = "ended"
        session.ended_at = session.ended_at or now
        session.updated_at = now
        _write_event(db, session, "ended", summary=_safe_text(getattr(payload, "reason", None) or "operator_end", max_chars=80))
    db.flush()
    return {"ok": True, "session": _serialize_session(session)}


def list_demo_events(db: Session, current_user: User, public_id: str) -> dict[str, Any]:
    settings = get_webcall_ai_demo_lab_settings()
    session = _load_demo_session(db, current_user, public_id)
    events = (
        db.query(WebchatEvent)
        .filter(WebchatEvent.conversation_id == session.conversation_id, WebchatEvent.event_type.like("webcall_ai_demo.%"))
        .order_by(WebchatEvent.id.desc())
        .limit(settings.demo_lab_event_retention_limit)
        .all()
    )
    turns = (
        db.query(WebchatVoiceAITurn)
        .filter(WebchatVoiceAITurn.voice_session_id == session.id)
        .order_by(WebchatVoiceAITurn.turn_index.asc())
        .limit(settings.demo_lab_event_retention_limit)
        .all()
    )
    return {
        "ok": True,
        "session": {"public_id": session.public_id, "status": session.status, "mode": session.mode},
        "events": [_serialize_event(event) for event in reversed(events)],
        "turns": [
            {
                "turn_id": turn.id,
                "turn_index": turn.turn_index,
                "customer_text_redacted": turn.customer_text_redacted,
                "ai_response_text_redacted": turn.ai_response_text_redacted,
                "handoff_required": turn.handoff_required,
                "created_at": format_utc(turn.created_at),
            }
            for turn in turns
        ],
    }


def _serialize_event(event: WebchatEvent) -> dict[str, Any]:
    try:
        payload = json.loads(event.payload_json or "{}")
    except json.JSONDecodeError:
        payload = {}
    event_type = str(payload.get("type") or event.event_type).replace("webcall_ai_demo.", "")
    return {
        "id": event.id,
        "type": event_type,
        "summary": str(payload.get("summary") or event_type)[:240],
        "created_at": format_utc(event.created_at),
    }
