from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, exists, or_, select

from ..models import Ticket
from ..models_sla_runtime import TicketSLATarget


def sla_risk_filter(now: datetime):
    """Return a portable per-Case SLA risk predicate.

    Normal risk instants are calculated from each immutable SLA assignment,
    business calendar, pause intervals and policy-specific risk window. A Case
    that still has bounded SLA due caches but no ``TicketSLATarget`` is treated
    as at risk because the canonical query projection is incomplete. This is a
    fail-safe integrity signal, not a second fixed-window SLA implementation.
    """

    target_exists = exists(
        select(TicketSLATarget.id).where(
            TicketSLATarget.ticket_id == Ticket.id
        )
    )
    target_at_risk = exists(
        select(TicketSLATarget.id).where(
            TicketSLATarget.ticket_id == Ticket.id,
            or_(
                and_(
                    Ticket.first_response_at.is_(None),
                    TicketSLATarget.first_response_risk_at <= now,
                ),
                TicketSLATarget.resolution_risk_at <= now,
            ),
        )
    )
    missing_projection = and_(
        ~target_exists,
        or_(
            Ticket.first_response_due_at.is_not(None),
            Ticket.resolution_due_at.is_not(None),
        ),
    )
    return or_(
        Ticket.first_response_breached.is_(True),
        Ticket.resolution_breached.is_(True),
        missing_projection,
        target_at_risk,
    )
