from __future__ import annotations

import hashlib
import json
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import ChannelAccount
from ..utils.time import utc_now
from ..voice_models import (
    TelephonyEventInbox,
    VoiceChannelConfiguration,
    WebchatVoiceSession,
)
from .storage import get_storage_backend
from .telephony_projection_service import (
    project_controller_event,
    project_livekit_event,
)

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 5
_TERMINAL_INBOX_STATUSES = {"processed", "ignored", "dead_letter"}


def _clean(value: Any, *, limit: int = 180) -> str | None:
    normalized = str(value or "").strip()
    return normalized[:limit] or None


def _participant(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("participant")
    return value if isinstance(value, dict) else {}


def _room(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("room")
    return value if isinstance(value, dict) else {}


def _attributes(payload: dict[str, Any]) -> dict[str, str]:
    value = _participant(payload).get("attributes")
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items() if item is not None}


def _room_name(payload: dict[str, Any]) -> str | None:
    return _clean(_room(payload).get("name") or payload.get("roomName"), limit=160)


def _provider_event_id(
    payload: dict[str, Any],
    *,
    raw_body: bytes,
    event_type: str,
) -> str:
    explicit = _clean(
        payload.get("id")
        or payload.get("eventId")
        or payload.get("event_id"),
        limit=180,
    )
    if explicit:
        return explicit
    room_name = _room_name(payload) or ""
    participant_identity = _clean(_participant(payload).get("identity"), limit=160) or ""
    created_at = str(payload.get("createdAt") or payload.get("created_at") or "")
    digest = hashlib.sha256(
        b"\x00".join(
            [
                event_type.encode("utf-8"),
                room_name.encode("utf-8"),
                participant_identity.encode("utf-8"),
                created_at.encode("utf-8"),
                raw_body,
            ]
        )
    ).hexdigest()
    return f"derived:{digest}"


def _safe_payload(
    payload: dict[str, Any],
    *,
    event_type: str,
) -> dict[str, Any]:
    attrs = _attributes(payload)
    participant = _participant(payload)
    room_name = _room_name(payload)
    caller = _clean(attrs.get("sip.phoneNumber"), limit=60)
    called = _clean(
        attrs.get("sip.trunkPhoneNumber") or attrs.get("sip.callTo"),
        limit=60,
    )
    call_id = _clean(attrs.get("sip.callID"), limit=180)
    return {
        "event_type": event_type,
        "room_name": room_name,
        "participant_identity_hash": (
            hashlib.sha256(
                str(participant.get("identity") or "").encode("utf-8")
            ).hexdigest()
            if participant.get("identity")
            else None
        ),
        "sip_call_id_hash": hashlib.sha256(call_id.encode("utf-8")).hexdigest() if call_id else None,
        "caller_number_hash": hashlib.sha256(caller.encode("utf-8")).hexdigest() if caller else None,
        "called_number_hash": hashlib.sha256(called.encode("utf-8")).hexdigest() if called else None,
        "sip_trunk_id": _clean(attrs.get("sip.trunkID"), limit=160),
        "sip_dispatch_rule_id": _clean(attrs.get("sip.ruleID"), limit=160),
        "sip_call_status": _clean(attrs.get("sip.callStatus"), limit=40),
        "provider_created_at": payload.get("createdAt") or payload.get("created_at"),
    }


def _controller_safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    identity = _clean(payload.get("controller_identity"), limit=160)
    return {
        "event_type": _clean(payload.get("event_type"), limit=80),
        "room_name": _clean(payload.get("room_name"), limit=160),
        "controller_identity_hash": hashlib.sha256(identity.encode("utf-8")).hexdigest() if identity else None,
        "command_reference": _clean(payload.get("command_reference"), limit=180),
        "provider_status": _clean(payload.get("provider_status"), limit=40),
        "provider_reason": _clean(payload.get("provider_reason"), limit=160),
        "safe_result": payload.get("safe_result") if isinstance(payload.get("safe_result"), dict) else {},
    }


def _load_existing(
    db: Session,
    *,
    provider: str,
    provider_event_id: str,
    lock: bool = False,
) -> TelephonyEventInbox | None:
    query = db.query(TelephonyEventInbox).filter(
        TelephonyEventInbox.provider == provider,
        TelephonyEventInbox.provider_event_id == provider_event_id,
    )
    if lock and db.bind and db.bind.dialect.name.startswith("postgresql"):
        query = query.with_for_update()
    return query.first()


def _create_inbox(
    db: Session,
    *,
    provider: str,
    provider_event_id: str,
    event_type: str,
    raw_body: bytes,
    safe_payload: dict[str, Any],
) -> tuple[TelephonyEventInbox, bool]:
    existing = _load_existing(
        db,
        provider=provider,
        provider_event_id=provider_event_id,
        lock=True,
    )
    if existing is not None:
        return existing, True
    row = TelephonyEventInbox(
        provider=provider,
        provider_event_id=provider_event_id,
        event_type=event_type,
        payload_sha256=hashlib.sha256(raw_body).hexdigest(),
        safe_payload_json=json.dumps(
            safe_payload,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ),
        status="received",
        attempt_count=0,
        received_at=utc_now(),
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        existing = _load_existing(
            db,
            provider=provider,
            provider_event_id=provider_event_id,
            lock=True,
        )
        if existing is None:
            raise
        return existing, True
    stored = get_storage_backend().persist_bytes(
        raw_body,
        original_filename=f"telephony-{provider}-{provider_event_id}.json",
        content_type="application/json",
    )
    row.raw_payload_object_key = stored.storage_key
    db.flush()
    return row, False


def _route_by_existing_session(
    db: Session,
    *,
    room_name: str | None,
) -> tuple[ChannelAccount, VoiceChannelConfiguration] | None:
    if not room_name:
        return None
    session = (
        db.query(WebchatVoiceSession)
        .filter(WebchatVoiceSession.provider_room_name == room_name)
        .order_by(WebchatVoiceSession.id.desc())
        .first()
    )
    if session is None or session.channel_account_id is None:
        return None
    account = db.get(ChannelAccount, session.channel_account_id)
    configuration = (
        db.query(VoiceChannelConfiguration)
        .filter(
            VoiceChannelConfiguration.channel_account_id == session.channel_account_id
        )
        .first()
    )
    if account is None or configuration is None:
        return None
    return account, configuration


def _unique_route(rows) -> tuple[ChannelAccount, VoiceChannelConfiguration] | None:
    unique: dict[int, tuple[ChannelAccount, VoiceChannelConfiguration]] = {
        account.id: (account, configuration) for account, configuration in rows
    }
    return next(iter(unique.values())) if len(unique) == 1 else None


def _resolve_livekit_route(
    db: Session,
    *,
    payload: dict[str, Any],
) -> tuple[ChannelAccount, VoiceChannelConfiguration] | None:
    by_session = _route_by_existing_session(db, room_name=_room_name(payload))
    if by_session is not None:
        return by_session
    attrs = _attributes(payload)
    did = _clean(
        attrs.get("sip.trunkPhoneNumber") or attrs.get("sip.callTo"),
        limit=60,
    )
    trunk_id = _clean(attrs.get("sip.trunkID"), limit=160)
    dispatch_rule_id = _clean(attrs.get("sip.ruleID"), limit=160)
    base_query = (
        db.query(ChannelAccount, VoiceChannelConfiguration)
        .join(
            VoiceChannelConfiguration,
            VoiceChannelConfiguration.channel_account_id == ChannelAccount.id,
        )
        .filter(
            ChannelAccount.provider == "voice",
            ChannelAccount.is_active.is_(True),
            VoiceChannelConfiguration.enabled.is_(True),
        )
    )
    if did:
        exact = base_query.filter(ChannelAccount.account_id == did).all()
        resolved = _unique_route(exact)
        if resolved is not None:
            return resolved
    if dispatch_rule_id:
        exact = base_query.filter(
            VoiceChannelConfiguration.dispatch_rule_id == dispatch_rule_id
        ).all()
        resolved = _unique_route(exact)
        if resolved is not None:
            return resolved
    if trunk_id:
        exact = base_query.filter(
            VoiceChannelConfiguration.inbound_trunk_id == trunk_id
        ).all()
        resolved = _unique_route(exact)
        if resolved is not None:
            return resolved
    return None


def _start_processing(row: TelephonyEventInbox) -> None:
    now = utc_now()
    row.status = "processing"
    row.attempt_count += 1
    row.processing_started_at = now
    row.next_attempt_at = None
    row.last_error_code = None


def _complete(
    row: TelephonyEventInbox,
    *,
    status_value: str,
    voice_session_id: int | None,
    error_code: str | None = None,
) -> None:
    now = utc_now()
    row.status = status_value
    row.voice_session_id = voice_session_id
    row.last_error_code = error_code
    row.processed_at = now
    row.processing_started_at = None
    row.next_attempt_at = None
    if status_value == "dead_letter":
        row.dead_lettered_at = now


def _retry(row: TelephonyEventInbox, *, error_code: str) -> None:
    now = utc_now()
    if row.attempt_count >= _MAX_ATTEMPTS:
        _complete(
            row,
            status_value="dead_letter",
            voice_session_id=row.voice_session_id,
            error_code=error_code,
        )
        return
    row.status = "retryable"
    row.last_error_code = error_code[:120]
    row.processing_started_at = None
    row.next_attempt_at = now + timedelta(
        seconds=min(5 * (2 ** max(row.attempt_count - 1, 0)), 300)
    )


def _result(
    row: TelephonyEventInbox,
    *,
    duplicate: bool,
) -> dict[str, Any]:
    return {
        "ok": row.status in {"processed", "ignored"},
        "idempotent": duplicate and row.status in _TERMINAL_INBOX_STATUSES,
        "event_id": row.provider_event_id,
        "status": row.status,
        "attempt_count": row.attempt_count,
        "tenant_id": row.tenant_id,
        "channel_account_id": row.channel_account_id,
        "voice_session_id": row.voice_session_id,
        "error_code": row.last_error_code,
    }


def process_livekit_webhook_event(
    db: Session,
    *,
    payload: dict[str, Any],
    raw_body: bytes,
) -> dict[str, Any]:
    event_type = str(payload.get("event") or payload.get("eventType") or "unknown").strip().lower()
    provider_event_id = _provider_event_id(
        payload,
        raw_body=raw_body,
        event_type=event_type,
    )
    row, duplicate = _create_inbox(
        db,
        provider="livekit",
        provider_event_id=provider_event_id,
        event_type=event_type,
        raw_body=raw_body,
        safe_payload=_safe_payload(payload, event_type=event_type),
    )
    if row.status in _TERMINAL_INBOX_STATUSES:
        return _result(row, duplicate=True)
    if row.status == "retryable" and row.next_attempt_at and row.next_attempt_at > utc_now():
        return _result(row, duplicate=duplicate)
    _start_processing(row)
    route = _resolve_livekit_route(db, payload=payload)
    if route is None:
        _complete(
            row,
            status_value="ignored",
            voice_session_id=None,
            error_code="unknown_or_ambiguous_voice_route",
        )
        db.flush()
        return _result(row, duplicate=duplicate)
    account, configuration = route
    row.tenant_id = account.tenant_id
    row.channel_account_id = account.id
    try:
        session = project_livekit_event(
            db,
            event_type=event_type,
            payload=payload,
            account=account,
            configuration=configuration,
        )
    except Exception as exc:
        logger.exception(
            "telephony_event_projection_failed",
            extra={
                "provider_event_id": provider_event_id,
                "event_type": event_type,
                "error_type": type(exc).__name__,
            },
        )
        _retry(row, error_code=f"projection:{type(exc).__name__}")
        db.flush()
        return _result(row, duplicate=duplicate)
    _complete(
        row,
        status_value="processed" if session is not None else "ignored",
        voice_session_id=session.id if session is not None else None,
        error_code=None if session is not None else "event_not_projected",
    )
    db.flush()
    return _result(row, duplicate=duplicate)


def process_controller_event(
    db: Session,
    *,
    payload: dict[str, Any],
    raw_body: bytes,
) -> dict[str, Any]:
    event_type = str(payload.get("event_type") or "unknown").strip().lower()
    provider_event_id = _clean(payload.get("event_id"), limit=180) or (
        "derived:" + hashlib.sha256(raw_body).hexdigest()
    )
    row, duplicate = _create_inbox(
        db,
        provider="livekit_agent",
        provider_event_id=provider_event_id,
        event_type=event_type,
        raw_body=raw_body,
        safe_payload=_controller_safe_payload(payload),
    )
    if row.status in _TERMINAL_INBOX_STATUSES:
        return _result(row, duplicate=True)
    if row.status == "retryable" and row.next_attempt_at and row.next_attempt_at > utc_now():
        return _result(row, duplicate=duplicate)
    _start_processing(row)
    room_name = _clean(payload.get("room_name"), limit=160)
    session = (
        db.query(WebchatVoiceSession)
        .filter(WebchatVoiceSession.provider_room_name == room_name)
        .order_by(WebchatVoiceSession.id.desc())
        .first()
        if room_name
        else None
    )
    if session is None or session.channel_account_id is None:
        _complete(
            row,
            status_value="ignored",
            voice_session_id=None,
            error_code="unknown_controller_room",
        )
        db.flush()
        return _result(row, duplicate=duplicate)
    account = db.get(ChannelAccount, session.channel_account_id)
    if account is None:
        _retry(row, error_code="controller_channel_missing")
        db.flush()
        return _result(row, duplicate=duplicate)
    row.tenant_id = account.tenant_id
    row.channel_account_id = account.id
    try:
        projected = project_controller_event(db, payload=payload)
    except Exception as exc:
        logger.exception(
            "telephony_controller_event_projection_failed",
            extra={
                "provider_event_id": provider_event_id,
                "event_type": event_type,
                "error_type": type(exc).__name__,
            },
        )
        _retry(row, error_code=f"controller_projection:{type(exc).__name__}")
        db.flush()
        return _result(row, duplicate=duplicate)
    _complete(
        row,
        status_value="processed" if projected is not None else "ignored",
        voice_session_id=projected.id if projected is not None else None,
        error_code=None if projected is not None else "controller_event_not_projected",
    )
    db.flush()
    return _result(row, duplicate=duplicate)
