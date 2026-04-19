from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..openclaw_quarantine_models import OpenClawUnresolvedEvent
from ..utils.time import utc_now


def quarantine_openclaw_event(
    db: Session,
    *,
    source: str,
    session_key: str | None,
    channel: str | None,
    account_id: str | None,
    thread_id: str | None,
    recipient: str | None,
    inferred_tenant_id: int | None,
    cursor_value: str | None,
    event_type: str,
    raw_event_json: dict[str, Any],
    route_json: dict[str, Any] | None,
    resolution_reason: str,
) -> OpenClawUnresolvedEvent:
    row = OpenClawUnresolvedEvent(
        source=source,
        session_key=session_key,
        channel=channel,
        account_id=account_id,
        thread_id=thread_id,
        recipient=recipient,
        inferred_tenant_id=inferred_tenant_id,
        cursor_value=cursor_value,
        event_type=event_type,
        raw_event_json=raw_event_json,
        route_json=route_json,
        resolution_status='quarantined',
        resolution_reason=resolution_reason,
    )
    db.add(row)
    db.flush()
    return row


def mark_quarantine_event_dropped(db: Session, *, event_id: int, reason: str) -> OpenClawUnresolvedEvent | None:
    row = db.query(OpenClawUnresolvedEvent).filter(OpenClawUnresolvedEvent.id == event_id).first()
    if row is None:
        return None
    row.resolution_status = 'dropped'
    row.resolution_reason = reason
    row.updated_at = utc_now()
    db.flush()
    return row


def replay_quarantined_event(db: Session, *, event_id: int) -> tuple[bool, str]:
    row = db.query(OpenClawUnresolvedEvent).filter(OpenClawUnresolvedEvent.id == event_id).first()
    if row is None:
        return False, 'not_found'
    row.replay_count += 1
    row.last_replayed_at = utc_now()
    row.updated_at = utc_now()
    db.flush()

    try:
        from .openclaw_bridge import _extract_event_session_key, _extract_route, _route_tenant_id, ensure_openclaw_conversation_link
        from ..models import Ticket
        from ..multi_tenant_models import TicketTenantLink

        event = row.raw_event_json or {}
        route = row.route_json if isinstance(row.route_json, dict) else {}
        session_key = row.session_key or _extract_event_session_key(event)
        recipient = row.recipient or (route.get('recipient') if isinstance(route, dict) else None)
        route_tenant_id = row.inferred_tenant_id or _route_tenant_id(db, route, row.account_id)
        if not session_key or not recipient or route_tenant_id is None:
            row.resolution_status = 'quarantined'
            row.resolution_reason = 'replay_missing_context'
            db.flush()
            return False, 'replay_missing_context'

        ticket = (
            db.query(Ticket)
            .join(TicketTenantLink, TicketTenantLink.ticket_id == Ticket.id)
            .filter(
                TicketTenantLink.tenant_id == route_tenant_id,
                ((Ticket.source_chat_id == recipient) | (Ticket.preferred_reply_contact == recipient)),
                Ticket.status.notin_(['resolved', 'closed', 'canceled'])
            )
            .order_by(Ticket.updated_at.desc())
            .first()
        )
        if ticket is None:
            row.resolution_status = 'quarantined'
            row.resolution_reason = 'replay_ticket_not_found'
            db.flush()
            return False, 'replay_ticket_not_found'

        ensure_openclaw_conversation_link(db, ticket=ticket, session_key=session_key, route=route)
        row.resolution_status = 'replayed'
        row.resolution_reason = 'replayed_successfully'
        db.flush()
        return True, 'replayed_successfully'
    except Exception as exc:
        row.resolution_status = 'quarantined'
        row.resolution_reason = f'replay_failed:{exc}'
        db.flush()
        return False, f'replay_failed:{exc}'
