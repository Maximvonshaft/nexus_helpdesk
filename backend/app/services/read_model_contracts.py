from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import case, or_
from sqlalchemy.orm import Session, joinedload

from ..enums import TicketPriority
from ..models import Ticket, User
from ..operator_models import OperatorTask
from .scope_permissions import has_global_case_visibility

_INSTALLED = False


def _control_tower_operator_tasks(db: Session, user: User, scope):
    """Use the Ticket relation already owned by ActorTenantQueryScope exactly once."""

    query = scope.operator_tasks(db)
    if not has_global_case_visibility(user, db):
        query = query.filter(
            or_(
                Ticket.team_id == user.team_id,
                Ticket.assignee_id == user.id,
                OperatorTask.assignee_id == user.id,
            )
        )
    return query


def _earliest_due_expression():
    return case(
        (
            Ticket.first_response_due_at.is_(None),
            Ticket.resolution_due_at,
        ),
        (
            Ticket.resolution_due_at.is_(None),
            Ticket.first_response_due_at,
        ),
        (
            Ticket.first_response_due_at <= Ticket.resolution_due_at,
            Ticket.first_response_due_at,
        ),
        else_=Ticket.resolution_due_at,
    )


def _sla_priority_rows(db: Session, user: User, now: datetime) -> list[dict[str, Any]]:
    from . import today_workbench_service as workbench

    due_at = _earliest_due_expression()
    breached_rank = case(
        (
            or_(
                Ticket.first_response_breached.is_(True),
                Ticket.resolution_breached.is_(True),
                due_at < now,
            ),
            0,
        ),
        else_=1,
    )
    priority_rank = case(
        (Ticket.priority == TicketPriority.urgent, 0),
        (Ticket.priority == TicketPriority.high, 1),
        (Ticket.priority == TicketPriority.medium, 2),
        else_=3,
    )

    rows = (
        workbench._active_tickets(workbench._visible_ticket_query(db, user))
        .options(
            joinedload(Ticket.customer),
            joinedload(Ticket.assignee),
            joinedload(Ticket.team),
        )
        .filter(
            or_(
                Ticket.first_response_due_at.is_not(None),
                Ticket.resolution_due_at.is_not(None),
                Ticket.first_response_breached.is_(True),
                Ticket.resolution_breached.is_(True),
            )
        )
        .order_by(
            breached_rank.asc(),
            due_at.asc().nullslast(),
            priority_rank.asc(),
            Ticket.created_at.asc(),
            Ticket.id.asc(),
        )
        .limit(6)
        .all()
    )
    return [
        {
            "ticket_id": ticket.id,
            "ticket_no": ticket.ticket_no,
            "title": ticket.issue_summary or ticket.title,
            "priority": workbench._value(ticket.priority),
            "status": workbench._value(ticket.status),
            "source_channel": workbench._value(ticket.source_channel),
            "customer_name": ticket.customer.name if ticket.customer else None,
            "assignee_name": (
                ticket.assignee.display_name if ticket.assignee else None
            ),
            "team_name": ticket.team.name if ticket.team else None,
            "resolution_due_at": (
                ticket.resolution_due_at.isoformat()
                if ticket.resolution_due_at
                else None
            ),
            "first_response_due_at": (
                ticket.first_response_due_at.isoformat()
                if ticket.first_response_due_at
                else None
            ),
            "minutes_to_due": workbench._minutes_to_due(ticket, now),
            "overdue": bool(
                ticket.first_response_breached
                or ticket.resolution_breached
                or ((workbench._sla_due_at(ticket) or now) < now)
            ),
            "href": "/workspace",
        }
        for ticket in rows
    ]


def install_read_model_contracts() -> None:
    """Install corrected read-model functions before any request is served."""

    global _INSTALLED
    if _INSTALLED:
        return
    from . import canonical_control_tower_service as control_tower
    from . import today_workbench_service as workbench

    control_tower._visible_operator_task_query = _control_tower_operator_tasks
    workbench._sla_priority_rows = _sla_priority_rows
    _INSTALLED = True


__all__ = ["install_read_model_contracts"]
