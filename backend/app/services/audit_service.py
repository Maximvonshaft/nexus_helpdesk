import json
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..enums import EventType
from ..models import AdminAuditLog, TicketEvent
from ..utils.time import utc_now
from .ticket_event_classification import resolve_ticket_event_class
from .ticket_event_writer import TicketEventClass, TicketEventWriter


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
    payload: Any = None,
    event_class: TicketEventClass | None = None,
) -> TicketEvent:
    # Only an absent payload means an intentionally empty mapping. Preserve
    # falsey non-mappings so the Writer emits bounded invalid evidence rather
    # than silently laundering them into a valid empty payload.
    resolved_payload = {} if payload is None else payload
    resolved_class = resolve_ticket_event_class(
        event_type,
        field_name=field_name,
        payload=resolved_payload,
        note=note,
        explicit=event_class,
    )
    return TicketEventWriter.add(
        db,
        ticket_id=ticket_id,
        actor_id=actor_id,
        event_type=event_type,
        event_class=resolved_class,
        field_name=field_name,
        old_value=old_value,
        new_value=new_value,
        note=note,
        payload=resolved_payload,
    )


def log_admin_audit(
    db: Session,
    *,
    actor_id: Optional[int],
    action: str,
    target_type: str,
    target_id: Optional[int] = None,
    old_value: Optional[dict[str, Any]] = None,
    new_value: Optional[dict[str, Any]] = None,
) -> AdminAuditLog:
    row = AdminAuditLog(
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        old_value_json=json.dumps(old_value, ensure_ascii=False) if old_value is not None else None,
        new_value_json=json.dumps(new_value, ensure_ascii=False) if new_value is not None else None,
        created_at=utc_now(),
    )
    db.add(row)
    db.flush()
    return row
