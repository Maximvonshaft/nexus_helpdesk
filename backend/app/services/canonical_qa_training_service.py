"""Capability-derived facade for QA and training projections."""

from sqlalchemy import or_

from ..models import Ticket
from . import qa_training_service as _legacy
from .scope_permissions import has_global_case_visibility


def _visible_ticket_query(db, user):
    query = db.query(Ticket)
    if not has_global_case_visibility(user, db):
        predicates = [Ticket.assignee_id == user.id]
        if user.team_id is not None:
            predicates.append(Ticket.team_id == user.team_id)
        query = query.filter(or_(*predicates))
    return query


def _with_ticket_visibility(query, user):
    db = query.session
    if has_global_case_visibility(user, db):
        return query
    predicates = [Ticket.assignee_id == user.id]
    if user.team_id is not None:
        predicates.append(Ticket.team_id == user.team_id)
    return query.filter(or_(*predicates))


_legacy._visible_ticket_query = _visible_ticket_query
_legacy._with_ticket_visibility = _with_ticket_visibility

from .qa_training_service import (  # noqa: E402
    build_qa_training,
    submit_agent_appeal,
    submit_knowledge_gap,
)

__all__ = ["build_qa_training", "submit_agent_appeal", "submit_knowledge_gap"]
