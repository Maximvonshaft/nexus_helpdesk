from __future__ import annotations

import hashlib
import json
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, or_
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
from .telephony_event_crypto import (
    open_telephony_event_payload,
    seal_telephony_event_payload,
)
from .telephony_projection_service import (
    project_controller_event,
    project_livekit_event,
)

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 5
_PROCESSING_LEASE_SECONDS = 90
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
    return _clean(
        _room(payload).get("name") or payload.get("roomName"),
        limit=160,
    )


def _provider_event_id(
    payload: dict[str, Any],
    *,
    raw_body: bytes,
    event_type: str,
) -> str:
    explicit = _clean(
        payload.get("id") or payload.get("eventId") or payload.get("event_id"),
        limit=180,
    )
    if explicit:
        return explicit
    room_name = _room_name(payload) or ""
    participant_identity = (
        _clean(_participant(payload).get("identity"), limit=160) or ""
    )
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
    caller = _clean(attrs.get("sip.phoneNumber"), limit=60)
    called = _clean(
        attrs.get("sip.trunkPhoneNumber") or attrs.get("sip.callTo"),
        limit=60,
    )
    call_id = _clean(attrs.get("sip.callID"), limit=180)
    identity = _clean(participant.get("identity"), limit=160)
    return {
        "event_type": event_type,
        "room_name": _room_name(payload),
        "participant_identity_hash": (
            hashlib.sha256(identity.encode("utf-8")).hexdigest()
            if identity
            else None
        ),
        "sip_call_id_hash": (
            hashlib.sha256(call_id.encode("utf-8")).hexdigest()
            if call_id
            else None
        ),
        "caller_number_hash": (
            hashlib.sha256(caller.encode("utf-8")).hexdigest()
            if caller
            else None
        ),
        "called_number_hash": (
            hashlib.sha256(called.encode("utf-8")).hexdigest()
            if called
            else None
        ),
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
        "controller_identity_hash": (
            hashlib.sha256(identity.encode("utf-8")).hexdigest()
            if identity
            else None
        ),
        "command_reference": _clean(
            payload.get("command_reference"),
            limit=180,
        ),
        "provider_status": _clean(payload.get("provider_status"), limit=40),
        "provider_reason": _clean(payload.get("provider_reason"), limit=160),
        "safe_result": (
            payload.get("safe_result")
            if isinstance(payload.get("safe_result"), dict)
            else {}
        ),
    }


def _event_envelope(
    *,
    safe_payload: dict[str, Any],
    full_payload: dict[str, Any],
) -> str:
    return json.dumps(
        {
            "version": 1,
            "safe": safe_payload,
            "sealed_payload": seal_telephony_event_payload(full_payload),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _open_event_envelope(row: TelephonyEventInbox) -> dict[str, Any]:
    try:
        envelope = json.loads(row.safe_payload_json or "{}")
        token = str(envelope["sealed_payload"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("telephony_event_envelope_invalid") from exc
    return open_telephony_event_payload(token)


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
    full_payload: dict[str, Any],
) -> tuple[TelephonyEventInbox, bool, bool]:
    payload_sha256 = hashlib.sha256(raw_body).hexdigest()
    existing = _load_existing(
        db,
        provider=provider,
        provider_event_id=provider_event_id,
        lock=True,
    )
    if existing is not None:
        return existing, True, existing.payload_sha256 != payload_sha256

    row = TelephonyEventInbox(
        provider=provider,
        provider_event_id=provider_event_id,
        event_type=event_type,
        payload_sha256=payload_sha256,
        safe_payload_json=_event_envelope(
            safe_payload=safe_payload,
            full_payload=full_payload,
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
        return existing, True, existing.payload_sha256 != payload_sha256

    stored = get_storage_backend().persist_bytes(
        content=raw_body,
        filename=f"telephony-{provider}-{payload_sha256[:24]}.json",
        media_type="application/json",
        allowed_mime_types={"application/json"},
        allowed_extensions={".json"},
        max_bytes=2 * 1024 * 1024,
    )
    row.raw_payload_object_key = stored.storage_key
    db.flush()
    return row, False, False


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
            VoiceChannelConfiguration.channel_account_id
            == session.channel_account_id
        )
        .first()
    )
    if account is None or configuration is None:
        return None
    return account, configuration


def _unique_route(rows) -> tuple[ChannelAccount, VoiceChannelConfiguration] | None:
    unique: dict[int, tuple[ChannelAccount, VoiceChannelConfiguration]] = {
        account.id: (account, configuration)
        for account, configuration in rows
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
        resolved = _unique_route(
            base_query.filter(ChannelAccount.account_id == did).all()
        )
        if resolved is not None:
            return resolved
    if dispatch_rule_id:
        resolved = _unique_route(
            base_query.filter(
                VoiceChannelConfiguration.dispatch_rule_id == dispatch_rule_id
            ).all()
        )
        if resolved is not None:
            return resolved
    if trunk_id:
        resolved = _unique_route(
            base_query.filter(
                VoiceChannelConfiguration.inbound_trunk_id == trunk_id
            ).all()
        )
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
    if row.attempt_count >= _MAX_ATTEMPTS:
        _complete(
            row,
            status_value="dead_letter",
            voice_session_id=row.voice_session_id,
            error_code=error_code[:120],
        )
        return
    row.status = "retryable"
    row.last_error_code = error_code[:120]
    row.processing_started_at = None
    row.next_attempt_at = utc_now() + timedelta(
        seconds=min(5 * (2 ** max(row.attempt_count - 1, 0)), 300)
    )


def _result(
    row: TelephonyEventInbox,
    *,
    duplicate: bool,
    payload_mismatch: bool = False,
) -> dict[str, Any]:
    return {
        "ok": row.status in {"processed", "ignored"} and not payload_mismatch,
        "idempotent": duplicate and row.status in _TERMINAL_INBOX_STATUSES,
        "payload_mismatch": payload_mismatch,
        "event_id": row.provider_event_id,
        "status": row.status,
        "attempt_count": row.attempt_count,
        "tenant_id": row.tenant_id,
        "channel_account_id": row.channel_account_id,
        "voice_session_id": row.voice_session_id,
        "error_code": (
            "provider_event_id_payload_mismatch"
            if payload_mismatch
            else row.last_error_code
        ),
    }


def _project_livekit_row(
    db: Session,
    *,
    row: TelephonyEventInbox,
    payload: dict[str, Any],
) -> None:
    route = _resolve_livekit_route(db, payload=payload)
    if route is None:
        _complete(
            row,
            status_value="ignored",
            voice_session_id=None,
            error_code="unknown_or_ambiguous_voice_route",
        )
        return
    account, configuration = route
    row.tenant_id = account.tenant_id
    row.channel_account_id = account.id
    try:
        with db.begin_nested():
            session = project_livekit_event(
                db,
                event_type=row.event_type,
                payload=payload,
                account=account,
                configuration=configuration,
            )
    except Exception as exc:
        logger.exception(
            "telephony_event_projection_failed",
            extra={"error_type": type(exc).__name__, "inbox_id": row.id},
        )
        _retry(row, error_code=f"projection:{type(exc).__name__}")
        return
    _complete(
        row,
        status_value="processed" if session is not None else "ignored",
        voice_session_id=session.id if session is not None else None,
        error_code=None if session is not None else "event_not_projected",
    )


def _project_controller_row(
    db: Session,
    *,
    row: TelephonyEventInbox,
    payload: dict[str, Any],
) -> None:
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
        return
    account = db.get(ChannelAccount, session.channel_account_id)
    if account is None:
        _retry(row, error_code="controller_channel_missing")
        return
    row.tenant_id = account.tenant_id
    row.channel_account_id = account.id
    try:
        with db.begin_nested():
            projected = project_controller_event(db, payload=payload)
    except Exception as exc:
        logger.exception(
            "telephony_controller_event_projection_failed",
            extra={"error_type": type(exc).__name__, "inbox_id": row.id},
        )
        _retry(
            row,
            error_code=f"controller_projection:{type(exc).__name__}",
        )
        return
    _complete(
        row,
        status_value="processed" if projected is not None else "ignored",
        voice_session_id=projected.id if projected is not None else None,
        error_code=(
            None if projected is not None else "controller_event_not_projected"
        ),
    )


def process_livekit_webhook_event(
    db: Session,
    *,
    payload: dict[str, Any],
    raw_body: bytes,
) -> dict[str, Any]:
    event_type = str(
        payload.get("event") or payload.get("eventType") or "unknown"
    ).strip().lower()
    provider_event_id = _provider_event_id(
        payload,
        raw_body=raw_body,
        event_type=event_type,
    )
    row, duplicate, payload_mismatch = _create_inbox(
        db,
        provider="livekit",
        provider_event_id=provider_event_id,
        event_type=event_type,
        raw_body=raw_body,
        safe_payload=_safe_payload(payload, event_type=event_type),
        full_payload=payload,
    )
    if payload_mismatch:
        logger.error(
            "telephony_event_id_payload_mismatch",
            extra={"inbox_id": row.id},
        )
        return _result(row, duplicate=True, payload_mismatch=True)
    if row.status in _TERMINAL_INBOX_STATUSES:
        return _result(row, duplicate=True)
    if (
        row.status == "retryable"
        and row.next_attempt_at
        and row.next_attempt_at > utc_now()
    ):
        return _result(row, duplicate=duplicate)
    _start_processing(row)
    _project_livekit_row(db, row=row, payload=payload)
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
    row, duplicate, payload_mismatch = _create_inbox(
        db,
        provider="livekit_agent",
        provider_event_id=provider_event_id,
        event_type=event_type,
        raw_body=raw_body,
        safe_payload=_controller_safe_payload(payload),
        full_payload=payload,
    )
    if payload_mismatch:
        logger.error(
            "telephony_event_id_payload_mismatch",
            extra={"inbox_id": row.id},
        )
        return _result(row, duplicate=True, payload_mismatch=True)
    if row.status in _TERMINAL_INBOX_STATUSES:
        return _result(row, duplicate=True)
    if (
        row.status == "retryable"
        and row.next_attempt_at
        and row.next_attempt_at > utc_now()
    ):
        return _result(row, duplicate=duplicate)
    _start_processing(row)
    _project_controller_row(db, row=row, payload=payload)
    db.flush()
    return _result(row, duplicate=duplicate)


def _claim_due_events(
    db: Session,
    *,
    limit: int,
) -> list[int]:
    now = utc_now()
    stale_before = now - timedelta(seconds=_PROCESSING_LEASE_SECONDS)
    due_retry = and_(
        TelephonyEventInbox.status == "retryable",
        TelephonyEventInbox.attempt_count < _MAX_ATTEMPTS,
        or_(
            TelephonyEventInbox.next_attempt_at.is_(None),
            TelephonyEventInbox.next_attempt_at <= now,
        ),
    )
    stale_processing = and_(
        TelephonyEventInbox.status == "processing",
        TelephonyEventInbox.attempt_count < _MAX_ATTEMPTS,
        TelephonyEventInbox.processing_started_at.isnot(None),
        TelephonyEventInbox.processing_started_at <= stale_before,
    )
    query = (
        db.query(TelephonyEventInbox)
        .filter(or_(due_retry, stale_processing))
        .order_by(
            TelephonyEventInbox.received_at.asc(),
            TelephonyEventInbox.id.asc(),
        )
    )
    if db.bind and db.bind.dialect.name.startswith("postgresql"):
        query = query.with_for_update(skip_locked=True)
    rows = query.limit(max(1, min(int(limit or 20), 100))).all()
    ids: list[int] = []
    for row in rows:
        _start_processing(row)
        ids.append(row.id)
    db.commit()
    return ids


def _reprocess_one(db: Session, *, inbox_id: int) -> None:
    row = db.get(TelephonyEventInbox, inbox_id)
    if row is None or row.status != "processing":
        return
    try:
        payload = _open_event_envelope(row)
    except Exception as exc:
        logger.exception(
            "telephony_event_replay_payload_invalid",
            extra={"inbox_id": inbox_id, "error_type": type(exc).__name__},
        )
        _complete(
            row,
            status_value="dead_letter",
            voice_session_id=row.voice_session_id,
            error_code="telephony_event_envelope_invalid",
        )
        db.commit()
        return

    if row.provider == "livekit":
        _project_livekit_row(db, row=row, payload=payload)
    elif row.provider == "livekit_agent":
        _project_controller_row(db, row=row, payload=payload)
    else:
        _complete(
            row,
            status_value="dead_letter",
            voice_session_id=row.voice_session_id,
            error_code="unsupported_telephony_event_provider",
        )
    db.commit()


def reprocess_due_telephony_events(
    db: Session,
    *,
    limit: int = 20,
) -> list[int]:
    inbox_ids = _claim_due_events(db, limit=limit)
    for inbox_id in inbox_ids:
        try:
            _reprocess_one(db, inbox_id=inbox_id)
        except Exception:
            db.rollback()
            logger.exception(
                "telephony_event_replay_failed",
                extra={"inbox_id": inbox_id},
            )
            row = db.get(TelephonyEventInbox, inbox_id)
            if row is not None and row.status == "processing":
                _retry(
                    row,
                    error_code="telephony_event_replay_internal_error",
                )
                db.commit()
    return inbox_ids
