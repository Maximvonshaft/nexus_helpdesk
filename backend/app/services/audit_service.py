import json
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..enums import EventType
from ..models import TicketEvent


def log_event(
    db: Session,
    *,
    ticket_id: int,
    actor_id: Optional[int],
    event_type: EventType,
    field_name: Optional[str] = None,
    old_value: Optional[str] = None,
    new_value: Optional[str] = None,
    note: Optional[str] = None,
    payload: Optional[dict[str, Any]] = None,
) -> TicketEvent:
    event = TicketEvent(
        ticket_id=ticket_id,
        actor_id=actor_id,
        event_type=event_type,
        field_name=field_name,
        old_value=old_value,
        new_value=new_value,
        note=note,
        payload_json=json.dumps(payload or {}, ensure_ascii=False),
    )
    db.add(event)
    db.flush()
    return event
