from __future__ import annotations

from datetime import datetime

from sqlalchemy import exists, or_, select

from ..models import Ticket
from ..models_sla_runtime import TicketSLATarget


def sla_risk_filter(now: datetime):
    """Return a portable per-Case SLA risk predicate.

    Risk instants are calculated from each immutable SLA assignment, business
    calendar, pause intervals and policy-specific risk window. No global
    30-minute approximation remains in operational queries.
    """

    target_at_risk = exists(
        select(TicketSLATarget.id).where(
            TicketSLATarget.ticket_id == Ticket.id,
            or_(
                (
                    Ticket.first_response_at.is_(None)
                    & (TicketSLATarget.first_response_risk_at <= now)
                ),
                TicketSLATarget.resolution_risk_at <= now,
            ),
        )
    )
    return or_(
        Ticket.first_response_breached.is_(True),
        Ticket.resolution_breached.is_(True),
        target_at_risk,
    )
