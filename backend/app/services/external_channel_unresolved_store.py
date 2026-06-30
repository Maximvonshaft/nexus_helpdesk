from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import ExternalChannelUnresolvedEvent
from ..utils.time import utc_now
from .external_channel_payload_hash import payload_hash as compute_payload_hash

ACTIVE_UNRESOLVED_STATUSES = ("pending", "failed", "replaying")


def _normalized_session_key(session_key: str | None) -> str:
    return session_key or ""


def _find_existing_active_row(
    db: Session,
    *,
    source: str,
    session_key: str | None,
    payload_hash: str,
) -> ExternalChannelUnresolvedEvent | None:
    normalized_session_key = _normalized_session_key(session_key)
    return (
        db.query(ExternalChannelUnresolvedEvent)
        .filter(
            ExternalChannelUnresolvedEvent.source == source,
            func.coalesce(ExternalChannelUnresolvedEvent.session_key, "") == normalized_session_key,
            ExternalChannelUnresolvedEvent.payload_hash == payload_hash,
            ExternalChannelUnresolvedEvent.status.in_(list(ACTIVE_UNRESOLVED_STATUSES)),
        )
        .order_by(ExternalChannelUnresolvedEvent.id.desc())
        .first()
    )


def persist_unresolved_external_channel_event_by_hash(
    db: Session,
    *,
    source: str,
    session_key: str | None,
    event_type: str | None,
    recipient: str | None,
    source_chat_id: str | None,
    preferred_reply_contact: str | None,
    payload: dict[str, Any],
) -> ExternalChannelUnresolvedEvent:
    """Persist unresolved ExternalChannel events using canonical payload hash idempotency.

    payload_json is still stored for replay/debug, but active dedupe must not rely
    on JSON text equality because key order and whitespace are not stable across
    producers.
    """
    payload_json = json.dumps(payload, ensure_ascii=False)
    current_payload_hash = compute_payload_hash(payload)
    existing = _find_existing_active_row(db, source=source, session_key=session_key, payload_hash=current_payload_hash)
    if existing is not None:
        existing.event_type = event_type
        existing.recipient = recipient
        existing.source_chat_id = source_chat_id
        existing.preferred_reply_contact = preferred_reply_contact
        existing.updated_at = utc_now()
        return existing
    row = ExternalChannelUnresolvedEvent(
        source=source,
        session_key=session_key,
        event_type=event_type,
        recipient=recipient,
        source_chat_id=source_chat_id,
        preferred_reply_contact=preferred_reply_contact,
        payload_json=payload_json,
        payload_hash=current_payload_hash,
        status="pending",
        replay_count=0,
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        existing = _find_existing_active_row(db, source=source, session_key=session_key, payload_hash=current_payload_hash)
        if existing is not None:
            existing.event_type = event_type
            existing.recipient = recipient
            existing.source_chat_id = source_chat_id
            existing.preferred_reply_contact = preferred_reply_contact
            existing.updated_at = utc_now()
            return existing
        raise
    return row


def apply_external_channel_unresolved_store_patch() -> None:
    """Rebind external_channel_bridge live path to payload_hash-backed persistence.

    This keeps the large bridge module stable while closing the runtime idempotency
    contract for every internal call that resolves the global function at runtime.
    """
    from . import external_channel_bridge

    external_channel_bridge.persist_unresolved_external_channel_event = persist_unresolved_external_channel_event_by_hash
