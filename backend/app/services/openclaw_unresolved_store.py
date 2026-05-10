from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from ..models import OpenClawUnresolvedEvent
from ..utils.time import utc_now
from .openclaw_payload_hash import payload_hash as compute_payload_hash

ACTIVE_UNRESOLVED_STATUSES = ("pending", "failed", "replaying")


def persist_unresolved_openclaw_event_by_hash(
    db: Session,
    *,
    source: str,
    session_key: str | None,
    event_type: str | None,
    recipient: str | None,
    source_chat_id: str | None,
    preferred_reply_contact: str | None,
    payload: dict[str, Any],
) -> OpenClawUnresolvedEvent:
    """Persist unresolved OpenClaw events using canonical payload hash idempotency.

    payload_json is still stored for replay/debug, but active dedupe must not rely
    on JSON text equality because key order and whitespace are not stable across
    producers.
    """
    payload_json = json.dumps(payload, ensure_ascii=False)
    current_payload_hash = compute_payload_hash(payload)
    existing = (
        db.query(OpenClawUnresolvedEvent)
        .filter(
            OpenClawUnresolvedEvent.source == source,
            OpenClawUnresolvedEvent.session_key == session_key,
            OpenClawUnresolvedEvent.payload_hash == current_payload_hash,
            OpenClawUnresolvedEvent.status.in_(list(ACTIVE_UNRESOLVED_STATUSES)),
        )
        .order_by(OpenClawUnresolvedEvent.id.desc())
        .first()
    )
    if existing is not None:
        existing.event_type = event_type
        existing.recipient = recipient
        existing.source_chat_id = source_chat_id
        existing.preferred_reply_contact = preferred_reply_contact
        existing.updated_at = utc_now()
        return existing
    row = OpenClawUnresolvedEvent(
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
    db.add(row)
    db.flush()
    return row


def apply_openclaw_unresolved_store_patch() -> None:
    """Rebind openclaw_bridge live path to payload_hash-backed persistence.

    This keeps the large bridge module stable while closing the runtime idempotency
    contract for every internal call that resolves the global function at runtime.
    """
    from . import openclaw_bridge

    openclaw_bridge.persist_unresolved_openclaw_event = persist_unresolved_openclaw_event_by_hash
