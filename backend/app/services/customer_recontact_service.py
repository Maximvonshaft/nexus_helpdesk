from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..enums import ConversationState, EventType, TicketStatus
from ..models import Ticket, TicketEvent
from ..models_agent_routing import ConversationControl
from ..utils.time import utc_now
from ..webchat_models import WebchatConversation
from .ticket_closure_readiness import invalidate_latest_closure_receipt
from .webchat_ai_turn_service import safe_write_webchat_event

_TERMINAL_TICKET_STATUSES = frozenset(
    {TicketStatus.resolved, TicketStatus.closed, TicketStatus.canceled}
)


@dataclass(frozen=True)
class TicketRecontactResult:
    ticket_reopened: bool
    closure_receipt_invalidated: bool


@dataclass(frozen=True)
class CustomerRecontactResult:
    conversation_reopened: bool
    ticket_reopened: bool
    closure_receipt_invalidated: bool


def reopen_ticket_from_customer_message(
    db: Session,
    *,
    ticket: Ticket | None,
    source: str,
    external_message_id: str,
    actor_id: int | None = None,
) -> TicketRecontactResult:
    """Restore Case responsibility and invalidate stale closure evidence once.

    Every channel adapter calls this command instead of directly mutating a
    terminal Ticket. The transition is idempotent for an already-open Ticket and
    keeps closure evidence, reopen count and lifecycle state in one authority.
    """

    if ticket is None or ticket.status not in _TERMINAL_TICKET_STATUSES:
        return TicketRecontactResult(
            ticket_reopened=False,
            closure_receipt_invalidated=False,
        )

    now = utc_now()
    source_value = str(source or "channel_intake").strip()[:80] or "channel_intake"
    message_identity = str(external_message_id or "").strip()[:180]
    invalidation = invalidate_latest_closure_receipt(
        db,
        ticket_id=ticket.id,
        actor_id=actor_id,
        reason=(
            f"Customer recontact via {source_value}; message identity "
            f"{message_identity or 'unavailable'}"
        ),
    )
    receipt_invalidated = invalidation is not None
    previous_status = ticket.status.value
    ticket.status = TicketStatus.pending_assignment
    ticket.reopen_count = int(ticket.reopen_count or 0) + 1
    ticket.closed_at = None
    ticket.resolved_at = None
    ticket.resolution_summary = None
    ticket.conversation_state = ConversationState.reopened_by_customer
    ticket.updated_at = now
    if invalidation is None:
        db.add(
            TicketEvent(
                ticket_id=ticket.id,
                actor_id=actor_id,
                event_type=EventType.reopened,
                field_name="customer_recontact",
                old_value=previous_status,
                new_value=TicketStatus.pending_assignment.value,
                note=f"Customer recontact received via {source_value}",
                payload_json=json.dumps(
                    {
                        "schema": "nexus.customer-recontact.v1",
                        "source": source_value,
                        "external_message_id": message_identity or None,
                        "closure_receipt_invalidated": False,
                        "contains_payloads": False,
                    },
                    sort_keys=True,
                ),
                created_at=now,
            )
        )
    db.flush()
    return TicketRecontactResult(
        ticket_reopened=True,
        closure_receipt_invalidated=receipt_invalidated,
    )


def reopen_from_customer_message(
    db: Session,
    *,
    conversation: WebchatConversation,
    control: ConversationControl,
    ticket: Ticket | None,
    source: str,
    external_message_id: str,
) -> CustomerRecontactResult:
    """Apply the sole Conversation + Case customer-recontact transition."""

    now = utc_now()
    source_value = str(source or "channel_intake").strip()[:80] or "channel_intake"
    message_identity = str(external_message_id or "").strip()[:180]
    conversation_reopened = bool(
        conversation.status != "open"
        or control.closed_at is not None
        or control.outcome is not None
    )
    if conversation_reopened:
        conversation.status = "open"
        conversation.updated_at = now
        conversation.last_seen_at = now
        control.outcome = None
        control.closed_at = None
        control.closed_by_user_id = None
        control.closure_note = None
        control.updated_at = now

    ticket_result = reopen_ticket_from_customer_message(
        db,
        ticket=ticket,
        source=source_value,
        external_message_id=message_identity,
        actor_id=None,
    )
    if conversation_reopened or ticket_result.ticket_reopened:
        safe_write_webchat_event(
            db,
            conversation_id=conversation.id,
            ticket_id=ticket.id if ticket is not None else None,
            event_type="customer.recontact_reopened",
            payload={
                "source": source_value,
                "external_message_id": message_identity or None,
                "ticket_reopened": ticket_result.ticket_reopened,
                "closure_receipt_invalidated": (
                    ticket_result.closure_receipt_invalidated
                ),
            },
        )
    db.flush()
    return CustomerRecontactResult(
        conversation_reopened=conversation_reopened,
        ticket_reopened=ticket_result.ticket_reopened,
        closure_receipt_invalidated=ticket_result.closure_receipt_invalidated,
    )


__all__ = [
    "CustomerRecontactResult",
    "TicketRecontactResult",
    "reopen_from_customer_message",
    "reopen_ticket_from_customer_message",
]
