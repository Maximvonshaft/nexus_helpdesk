"""Canonical public facade for ticket-domain operations.

The private implementation owns ticket mechanics. This public authority owns the
cross-domain Safe Effective Closure gate so no API, compatibility import or UI
can close a ticket from status alone.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..enums import TicketStatus
from ..models import Ticket, User
from ..schemas import TicketReopenRequest, TicketStatusChangeRequest
from . import ticket_service_core as _core
from .ticket_closure_readiness import (
    append_closure_receipt_event,
    invalidate_latest_closure_receipt,
    require_closure_ready,
)
from .ticket_service_core import *  # noqa: F401,F403


def change_status(
    db: Session,
    ticket_id: int,
    payload: TicketStatusChangeRequest,
    current_user: User,
) -> Ticket:
    receipt = None
    if payload.new_status == TicketStatus.closed:
        ticket = _core.get_ticket_or_404(db, ticket_id)
        receipt = require_closure_ready(db, ticket)

    ticket = _core.change_status(db, ticket_id, payload, current_user)
    if receipt is not None:
        append_closure_receipt_event(
            db,
            ticket_id=ticket.id,
            actor_id=current_user.id,
            receipt=receipt,
        )
        ticket = _core.get_ticket_or_404(db, ticket.id)
    return ticket


def reopen_ticket(
    db: Session,
    ticket_id: int,
    payload: TicketReopenRequest,
    current_user: User,
) -> Ticket:
    ticket = _core.reopen_ticket(db, ticket_id, payload, current_user)
    invalidate_latest_closure_receipt(
        db,
        ticket_id=ticket.id,
        actor_id=current_user.id,
        reason=payload.reason,
    )
    return _core.get_ticket_or_404(db, ticket.id)


def __getattr__(name: str):
    return getattr(_core, name)


__all__ = [
    "add_ai_intake",
    "add_attachment",
    "add_comment",
    "add_internal_note",
    "assign_ticket",
    "change_status",
    "create_ticket",
    "escalate_ticket",
    "get_customer_history",
    "get_ticket_events",
    "get_ticket_or_404",
    "get_ticket_stats",
    "list_tickets",
    "reopen_ticket",
    "save_outbound_draft",
    "send_outbound_message",
    "update_ticket",
    "validate_assignee_team",
]
