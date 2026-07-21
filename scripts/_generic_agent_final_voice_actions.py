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
    "record_admin_voice_action",
    '''def record_admin_voice_action(
    db: Session,
    *,
    voice_session_public_id: str,
    current_user: User,
    action_type: str,
    target: str | None = None,
    digits: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    ensure_can_control_webcall_voice(current_user, db)
    session, _conversation, ticket = _visible_voice_session_context(
        db,
        voice_session_public_id=voice_session_public_id,
        current_user=current_user,
    )

    requested = (action_type or "").strip().lower()
    if requested not in CALL_CONTROL_ACTIONS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="unsupported webcall voice action")
    if session.status in TERMINAL_STATUSES:
        raise _conflict("voice session already closed")
    if requested in {"hold", "resume", "keypad", "transfer", "add_participant"} and session.status not in CALL_CONTROL_ACTIVE_STATUSES:
        raise _conflict("voice session action requires an active call")
    if requested == "keypad" and not digits:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="keypad digits are required")
    if requested in {"transfer", "add_participant"} and not target:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="action target is required")

    now = utc_now()
    safe_payload = _safe_action_payload(requested, target=target, digits=digits, note=note)
    provider_status = "not_executed"
    provider_reason = "provider_adapter_pending"
    action = WebchatVoiceSessionAction(
        voice_session_id=session.id,
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        actor_user_id=current_user.id,
        action_type=requested,
        status="recorded",
        provider_status=provider_status,
        provider_reason=provider_reason,
        payload_json=json.dumps(safe_payload, ensure_ascii=False, sort_keys=True),
        created_at=now,
    )
    db.add(action)
    db.flush()
    event_payload = {
        "voice_session_id": session.public_id,
        "action_id": action.id,
        "action_type": requested,
        "status": action.status,
        "provider": session.provider,
        "provider_status": provider_status,
        "provider_reason": provider_reason,
        "payload": safe_payload,
    }
    ticket_event = None
    if ticket is not None:
        ticket_event = log_event(
            db,
            ticket_id=ticket.id,
            actor_id=current_user.id,
            event_type=EventType.field_updated,
            field_name="webcall.voice.action",
            new_value=requested,
            note="WebCall session action recorded",
            payload=event_payload,
        )
    webchat_event = _write_voice_event(
        db,
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        event_type="voice.session.action_recorded",
        payload={**event_payload, "actor_user_id": current_user.id},
    )
    audit = log_admin_audit(
        db,
        actor_id=current_user.id,
        action=f"webcall.voice.action.{requested}",
        target_type="webchat_voice_session_action",
        target_id=action.id,
        old_value=None,
        new_value={**event_payload, "ticket_id": session.ticket_id},
    )
    action.ticket_event_id = ticket_event.id if ticket_event is not None else None
    action.webchat_event_id = webchat_event.id
    action.audit_id = audit.id
    session.updated_at = now
    db.flush()
    return {
        "ok": True,
        "ticket_id": session.ticket_id,
        "voice_session_id": session.public_id,
        "action": _serialize_session_action(action),
    }''',
)

voice = replace_function(
    voice,
    "save_admin_voice_note",
    '''def save_admin_voice_note(
    db: Session,
    *,
    voice_session_public_id: str,
    current_user: User,
    body: str,
    source: str | None = None,
) -> dict[str, Any]:
    ensure_can_read_webcall_voice(current_user, db)
    ensure_can_write_internal_note(current_user, db)
    session, _conversation, ticket = _visible_voice_session_context(
        db,
        voice_session_public_id=voice_session_public_id,
        current_user=current_user,
    )
    normalized_body = (body or "").strip()
    if not normalized_body:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="voice note body is required")

    now = utc_now()
    source_value = (source or "webcall_operator_workbench").strip() or "webcall_operator_workbench"
    note_record = WebchatVoiceSessionAction(
        voice_session_id=session.id,
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        actor_user_id=current_user.id,
        action_type="note",
        status="recorded",
        provider_status="not_applicable",
        provider_reason="internal_note",
        payload_json=json.dumps({"body": normalized_body, "source": source_value}, ensure_ascii=False),
        created_at=now,
    )
    db.add(note_record)
    db.flush()

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
            note="WebCall call note saved",
            payload={
                "voice_session_id": session.public_id,
                "note_id": ticket_note.id,
                "voice_note_id": note_record.id,
                "source": source_value,
                "provider": session.provider,
                "status": session.status,
            },
        )

    safe_payload = {
        "voice_session_id": session.public_id,
        "voice_note_id": note_record.id,
        "ticket_note_id": ticket_note.id if ticket_note is not None else None,
        "source": source_value,
        "provider": session.provider,
        "status": session.status,
        "body_length": len(normalized_body),
    }
    webchat_event = _write_voice_event(
        db,
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        event_type="voice.session.note_saved",
        payload={**safe_payload, "author_id": current_user.id},
    )
    audit = log_admin_audit(
        db,
        actor_id=current_user.id,
        action="webcall.voice.note_saved",
        target_type="webchat_voice_session",
        target_id=session.id,
        old_value=None,
        new_value={**safe_payload, "ticket_id": session.ticket_id},
    )
    note_record.ticket_event_id = ticket_event.id if ticket_event is not None else None
    note_record.webchat_event_id = webchat_event.id
    note_record.audit_id = audit.id
    db.flush()
    return {
        "ok": True,
        "ticket_id": session.ticket_id,
        "voice_session_id": session.public_id,
        "note_id": ticket_note.id if ticket_note is not None else note_record.id,
        "ticket_event_id": ticket_event.id if ticket_event is not None else None,
        "webchat_event_id": webchat_event.id,
        "audit_id": audit.id,
        "created_at": note_record.created_at.isoformat(),
    }''',
)

callback_start, callback_end = function_bounds(voice, "queue_speedaf_voice_callback")
callback = voice[callback_start:callback_end]
callback = callback.replace("    ticket_id: int,\n", "", 1)
callback = replace_once(
    callback,
    "    session = _load_voice_session(db, voice_session_public_id)\n"
    "    if session.ticket_id != ticket_id:\n"
    "        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=\"webchat voice session not found\")\n"
    "    _ensure_ticket_visible_for_session(db, current_user, session)\n",
    "    session, _conversation, ticket = _visible_voice_session_context(\n"
    "        db,\n"
    "        voice_session_public_id=voice_session_public_id,\n"
    "        current_user=current_user,\n"
    "    )\n"
    "    if ticket is None:\n"
    "        raise HTTPException(\n"
    "            status_code=status.HTTP_409_CONFLICT,\n"
    "            detail=\"formal_ticket_required_for_voice_business_action\",\n"
    "        )\n"
    "    ticket_id = ticket.id\n",
    label="voice callback derives formal ticket",
)
voice = voice[:callback_start] + callback + voice[callback_end:]

write(voice_service_path, voice)
