from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import or_

from ..models import Ticket

DEFAULT_SLA_RISK_WINDOW = timedelta(minutes=30)


def sla_risk_filter(now: datetime, *, window: timedelta = DEFAULT_SLA_RISK_WINDOW):
    deadline = now + window
    return or_(
        Ticket.first_response_due_at <= deadline,
        Ticket.resolution_due_at <= deadline,
        Ticket.first_response_breached.is_(True),
        Ticket.resolution_breached.is_(True),
    )
