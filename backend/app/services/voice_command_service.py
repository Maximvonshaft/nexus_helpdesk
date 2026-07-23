from __future__ import annotations

import hashlib
import json
import secrets
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import User
from ..utils.time import utc_now
from ..voice_models import WebchatVoiceSession, WebchatVoiceSessionAction
from ..webchat_models import WebchatEvent
from .audit_service import log_admin_audit
from .voice_command_crypto import seal_voice_command_payload

SUPPORTED_COMMANDS = {
    "ai_suspend",
    "hangup",
    "hold",
    "resume",
    "mute",
    "unmute",
    "keypad",
    "add_participant",
    "remove_participant",
    "cold_transfer",
    "warm_transfer",
    "recording_start",
    "recording_stop",
}


def _actor_id(actor: User | None) -> int | None:
    return actor.id if actor is not None else None


def _safe_request(
    action_type: str,
    *,
    target: str | None,
    digits: str | None,
    note: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"action_type": action_type}
    if target:
        payload["target"] = {
            "redacted": True,
            "sha256_prefix": hashlib.sha256(
                target.encode("utf-8")
            ).hexdigest()[:16],
            "suffix": target[-4:] if len(target) >= 4 else "",
        }
    if digits:
        payload["digits"] = {"redacted": True, "length": len(digits)}
    if note:
        payload["note_present"] = True
        payload["note_length"] = len(note)
    return payload


def _validate_command(
    action_type: str,
    *,
    target: str | None,
    digits: str | None,
) -> str:
    requested = str(action_type or "").strip().lower()
    if requested not in SUPPORTED_COMMANDS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="unsupported webcall voice action",
        )
    if requested == "keypad" and not digits:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="keypad digits are required",
        )
    if requested in {
        "add_participant",
        "remove_participant",
        "cold_transfer",
        "warm_transfer",
    } and not target:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="action target is required",
        )
    return requested


def _resolve_existing_command(
    db: Session,
    *,
    idempotency_key: str,
    session: WebchatVoiceSession,
    actor: User | None,
) -> WebchatVoiceSessionAction | None:
    existing = (
        db.query(WebchatVoiceSessionAction)
        .filter(WebchatVoiceSessionAction.idempotency_key == idempotency_key)
        .first()
    )
    if existing is None:
        return None
    if (
        existing.voice_session_id != session.id
        or existing.actor_user_id != _actor_id(actor)
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="voice action idempotency conflict",
        )
    return existing


def enqueue_voice_command(
    db: Session,
    *,
    session: WebchatVoiceSession,
    actor: User | None,
    action_type: str,
    target: str | None = None,
    digits: str | None = None,
    note: str | None = None,
    idempotency_key: str | None = None,
) -> WebchatVoiceSessionAction:
    requested = _validate_command(action_type, target=target, digits=digits)
    provided_key = (idempotency_key or "").strip()[:160]
    key = provided_key or (
        f"voice-command:{session.id}:{secrets.token_urlsafe(24)}"
    )[:160]
    if provided_key:
        existing = _resolve_existing_command(
            db,
            idempotency_key=key,
            session=session,
            actor=actor,
        )
        if existing is not None:
            return existing

    now = utc_now()
    actor_id = _actor_id(actor)
    actor_type = "operator" if actor is not None else "system"
    safe_request = _safe_request(
        requested,
        target=target,
        digits=digits,
        note=note,
    )
    command = WebchatVoiceSessionAction(
        public_id=f"vc_{secrets.token_urlsafe(18)}",
        voice_session_id=session.id,
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        actor_user_id=actor_id,
        action_type=requested,
        idempotency_key=key,
        status="requested",
        provider_status="pending",
        attempt_count=0,
        next_attempt_at=now,
        payload_json=seal_voice_command_payload(
            {"target": target, "digits": digits, "note": note}
        ),
        result_json=json.dumps(
            {"safe_request": safe_request},
            ensure_ascii=False,
            sort_keys=True,
        ),
        created_at=now,
        updated_at=now,
    )
    try:
        with db.begin_nested():
            db.add(command)
            db.flush()
    except IntegrityError:
        existing = _resolve_existing_command(
            db,
            idempotency_key=key,
            session=session,
            actor=actor,
        )
        if existing is not None:
            return existing
        raise

    event = WebchatEvent(
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        event_type="voice.command.requested",
        payload_json=json.dumps(
            {
                "voice_session_id": session.public_id,
                "command_id": command.public_id,
                "action_type": requested,
                "actor_user_id": actor_id,
                "actor_type": actor_type,
                "safe_request": safe_request,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        created_at=now,
    )
    db.add(event)
    db.flush()
    audit = log_admin_audit(
        db,
        actor_id=actor_id,
        action=f"webcall.voice.command.{requested}.requested",
        target_type="webchat_voice_command",
        target_id=command.id,
        old_value=None,
        new_value={
            "voice_session_id": session.public_id,
            "command_id": command.public_id,
            "actor_type": actor_type,
            "safe_request": safe_request,
        },
    )
    command.webchat_event_id = event.id
    command.audit_id = audit.id
    db.flush()
    return command


def serialize_voice_command(
    command: WebchatVoiceSessionAction,
) -> dict[str, Any]:
    try:
        result = json.loads(command.result_json or "{}")
    except json.JSONDecodeError:
        result = {}
    return {
        "id": command.public_id,
        "action_type": command.action_type,
        "idempotency_key": command.idempotency_key,
        "status": command.status,
        "provider_status": command.provider_status,
        "provider_reason": command.provider_reason,
        "provider_reference": command.provider_reference,
        "attempt_count": command.attempt_count,
        "result": result,
        "actor_user_id": command.actor_user_id,
        "completed_at": (
            command.completed_at.isoformat()
            if command.completed_at
            else None
        ),
        "next_attempt_at": (
            command.next_attempt_at.isoformat()
            if command.next_attempt_at
            else None
        ),
        "created_at": (
            command.created_at.isoformat()
            if command.created_at
            else None
        ),
    }
