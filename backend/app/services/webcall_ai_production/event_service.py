from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from ...utils.time import utc_now
from ...webchat_models import WebchatEvent
from .metrics import record_webcall_ai_event


def write_event(
    db: Session,
    *,
    conversation_id: int,
    ticket_id: int,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> WebchatEvent:
    event = WebchatEvent(
        conversation_id=conversation_id,
        ticket_id=ticket_id,
        event_type=event_type,
        payload_json=json.dumps(payload or {}, ensure_ascii=False),
        created_at=utc_now(),
    )
    db.add(event)
    if event_type.startswith("webcall_ai."):
        record_webcall_ai_event(event_type)
    return event


def serialize_event(event: WebchatEvent) -> dict[str, Any]:
    try:
        payload = json.loads(event.payload_json or "{}")
    except Exception:
        payload = {}
    return {
        "id": event.id,
        "event_type": event.event_type,
        "payload": payload,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }
