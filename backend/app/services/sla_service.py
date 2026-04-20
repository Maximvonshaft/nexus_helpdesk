from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from ..enums import EventType, TicketPriority, TicketStatus
from ..models import SLAPolicy, Ticket
from ..utils.time import ensure_utc, utc_now
from .audit_service import log_event


def seed_default_sla_policies(db: Session):
    defaults = {
        TicketPriority.low: (240, 2880),
        TicketPriority.medium: (120, 1440),
        TicketPriority.high: (60, 720),
        TicketPriority.urgent: (15, 240),
    }
    for priority, (first_minutes, resolution_minutes) in defaults.items():
        existing = db.query(SLAPolicy).filter(SLAPolicy.priority == priority).first()
        if not existing:
            db.add(
                SLAPolicy(
                    name=f"{priority.value.title()} SLA",
                    priority=priority,
                    first_response_minutes=first_minutes,
                    resolution_minutes=resolution_minutes,
                    pause_on_waiting_customer=True,
                    pause_on_waiting_internal=False,
                )
            )
    db.commit()


def get_policy_for_priority(db: Session, priority: TicketPriority) -> Optional[SLAPolicy]:
    return db.query(SLAPolicy).filter(SLAPolicy.priority == priority).first()


def apply_policy_to_ticket(ticket: Ticket, policy: SLAPolicy, now: Optional[datetime] = None):
    now = ensure_utc(now) or utc_now()
    ticket.sla_policy_id = policy.id
    base = ensure_utc(ticket.created_at) or now
    pause_delta = timedelta(seconds=ticket.total_paused_seconds or 0)
    current_pause = timedelta(seconds=0)
    paused_at = ensure_utc(ticket.sla_paused_at)
    if ticket.sla_paused and paused_at:
        current_pause = now - paused_at
    ticket.first_response_due_at = base + timedelta(minutes=policy.first_response_minutes) + pause_delta + current_pause
    ticket.resolution_due_at = base + timedelta(minutes=policy.resolution_minutes) + pause_delta + current_pause


def pause_sla(ticket: Ticket, reason: str):
    if ticket.sla_paused:
        return
    ticket.sla_paused = True
    ticket.sla_paused_at = utc_now()
    ticket.sla_pause_reason = reason


def resume_sla(ticket: Ticket):
    if not ticket.sla_paused:
        return
    now = utc_now()
    paused_at = ensure_utc(ticket.sla_paused_at)
    if paused_at:
        ticket.total_paused_seconds += int((now - paused_at).total_seconds())
    ticket.sla_paused = False
    ticket.sla_paused_at = None
    ticket.sla_pause_reason = None


def update_pause_state_for_status(ticket: Ticket, new_status: TicketStatus, db: Session):
    policy = ticket.sla_policy or get_policy_for_priority(db, ticket.priority)
    if not policy:
        return

    should_pause = False
    reason = None
    if new_status == TicketStatus.waiting_customer and policy.pause_on_waiting_customer:
        should_pause = True
        reason = "waiting_customer"
    elif new_status == TicketStatus.waiting_internal and policy.pause_on_waiting_internal:
        should_pause = True
        reason = "waiting_internal"

    if should_pause:
        pause_sla(ticket, reason or "manual_pause")
    else:
        resume_sla(ticket)

    apply_policy_to_ticket(ticket, policy)


def update_first_response(ticket: Ticket):
    if ticket.first_response_at is None:
        ticket.first_response_at = utc_now()


def compute_sla_snapshot(ticket: Ticket, db: Session | None = None) -> dict[str, bool]:
    now = utc_now()
    policy = ticket.sla_policy
    if policy is None and db is not None:
        policy = get_policy_for_priority(db, ticket.priority)
    if policy and (ticket.first_response_due_at is None or ticket.resolution_due_at is None):
        apply_policy_to_ticket(ticket, policy, now=now)

    first_due = ensure_utc(ticket.first_response_due_at)
    resolution_due = ensure_utc(ticket.resolution_due_at)
    first_at = ensure_utc(ticket.first_response_at)
    status = ticket.status

    first_breached = bool(ticket.first_response_breached or (first_at is None and first_due and now > first_due))
    resolution_breached = bool(
        ticket.resolution_breached
        or (status not in {TicketStatus.closed, TicketStatus.canceled} and resolution_due and now > resolution_due)
    )
    overdue = bool(status not in {TicketStatus.closed, TicketStatus.canceled} and resolution_due and now > resolution_due)
    return {
        "first_response_breached": first_breached,
        "resolution_breached": resolution_breached,
        "overdue": overdue,
    }


def evaluate_sla(ticket: Ticket, db: Session):
    previous_first = ticket.first_response_breached
    previous_resolution = ticket.resolution_breached
    snapshot = compute_sla_snapshot(ticket, db)
    ticket.first_response_breached = snapshot["first_response_breached"]
    ticket.resolution_breached = snapshot["resolution_breached"]

    if ticket.first_response_breached and not previous_first:
        log_event(
            db,
            ticket_id=ticket.id,
            actor_id=None,
            event_type=EventType.sla_breached,
            note="First response SLA breached",
            payload={"scope": "first_response"},
        )

    if ticket.resolution_breached and not previous_resolution:
        log_event(
            db,
            ticket_id=ticket.id,
            actor_id=None,
            event_type=EventType.sla_breached,
            note="Resolution SLA breached",
            payload={"scope": "resolution"},
        )
