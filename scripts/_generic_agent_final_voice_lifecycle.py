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
voice = replace_function(
    voice,
    "accept_admin_voice_session",
    '''def accept_admin_voice_session(
    db: Session,
    *,
    voice_session_public_id: str,
    current_user: User,
) -> dict[str, Any]:
    ensure_can_accept_webcall_voice(current_user, db)
    session, _conversation, _ticket = _visible_voice_session_context(
        db,
        voice_session_public_id=voice_session_public_id,
        current_user=current_user,
    )
    now = utc_now()
    if _mark_missed_if_expired(db, session=session, now=now):
        db.flush()
        raise _conflict(DETAIL_EXPIRED)
    if session.status in TERMINAL_STATUSES:
        raise _conflict(_closed_accept_detail(session.status))
    if session.accepted_by_user_id is not None and session.accepted_by_user_id != current_user.id:
        raise _conflict(DETAIL_ALREADY_ACCEPTED_BY_OTHER)
    if session.status not in ACCEPT_READY_STATUSES and session.status not in ACCEPTED_STATUSES:
        raise _conflict(DETAIL_NOT_ACCEPTABLE)

    first_accept = session.accepted_by_user_id is None
    session.status = "active"
    session.accepted_by_user_id = current_user.id
    session.accepted_at = session.accepted_at or now
    session.active_at = session.active_at or now
    session.updated_at = now
    value, ttl, identity = _issue_token(session, "agent", str(current_user.id))
    existing = db.query(WebchatVoiceParticipant).filter(
        WebchatVoiceParticipant.voice_session_id == session.id,
        WebchatVoiceParticipant.provider_identity == identity,
    ).first()
    if existing is None:
        db.add(WebchatVoiceParticipant(
            voice_session_id=session.id,
            participant_type="agent",
            user_id=current_user.id,
            provider_identity=identity,
            status="invited",
            created_at=now,
        ))
    if first_accept:
        _write_voice_event(
            db,
            conversation_id=session.conversation_id,
            ticket_id=session.ticket_id,
            event_type="voice.session.accepted",
            payload={"voice_session_id": session.public_id, "accepted_by_user_id": current_user.id},
        )
        _emit_voice_observability(session, "voice.session.accepted")
        _write_voice_event(
            db,
            conversation_id=session.conversation_id,
            ticket_id=session.ticket_id,
            event_type="voice.session.active",
            payload={"voice_session_id": session.public_id, "accepted_by_user_id": current_user.id},
        )
        _emit_voice_observability(session, "voice.session.active")
    db.flush()
    return _serialize_session(session, participant_token=value, expires_in_seconds=ttl, participant_identity=identity)''',
)

voice = replace_function(
    voice,
    "reject_admin_voice_session",
    '''def reject_admin_voice_session(
    db: Session,
    *,
    voice_session_public_id: str,
    current_user: User,
    reason: str | None = None,
) -> dict[str, Any]:
    ensure_can_reject_webcall_voice(current_user, db)
    session, _conversation, _ticket = _visible_voice_session_context(
        db,
        voice_session_public_id=voice_session_public_id,
        current_user=current_user,
    )
    now = utc_now()
    if _mark_missed_if_expired(db, session=session, now=now):
        db.flush()
        raise _conflict(DETAIL_EXPIRED)
    if session.status in TERMINAL_STATUSES:
        return {"ok": True, "status": session.status, "voice_session_id": session.public_id, "accepted_by_user_id": session.accepted_by_user_id}
    if session.accepted_by_user_id is not None or session.status in ACCEPTED_STATUSES:
        raise _conflict(DETAIL_ALREADY_ACTIVE)
    if session.status not in REJECT_READY_STATUSES:
        raise _conflict(DETAIL_NOT_REJECTABLE)

    session.status = "cancelled"
    session.ended_at = session.ended_at or now
    session.ended_by_user_id = current_user.id
    session.updated_at = now
    _close_provider_room_for_session(session)
    _write_voice_event(
        db,
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        event_type="voice.session.rejected",
        payload={"voice_session_id": session.public_id, "rejected_by_user_id": current_user.id, "reason": (reason or None)},
    )
    _emit_voice_observability(session, "voice.session.rejected")
    _ensure_final_voice_call_message(db, session=session)
    db.flush()
    return {"ok": True, "status": session.status, "voice_session_id": session.public_id, "accepted_by_user_id": session.accepted_by_user_id}''',
)

voice = replace_function(
    voice,
    "end_admin_voice_session",
    '''def end_admin_voice_session(
    db: Session,
    *,
    voice_session_public_id: str,
    current_user: User,
) -> dict[str, Any]:
    ensure_can_end_webcall_voice(current_user, db)
    session, _conversation, _ticket = _visible_voice_session_context(
        db,
        voice_session_public_id=voice_session_public_id,
        current_user=current_user,
    )
    _end_voice_session(db, session=session, ended_by_user_id=current_user.id)
    return {"ok": True, "status": session.status, "voice_session_id": session.public_id, "accepted_by_user_id": session.accepted_by_user_id}''',
)
write(voice_service_path, voice)
