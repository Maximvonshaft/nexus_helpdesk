"""Canonical QA training service with capability-derived case visibility."""

from sqlalchemy import or_
from sqlalchemy.orm import Query, Session

from ..models import Ticket, User
from . import qa_training_service_core as _core
from .permissions import has_global_case_visibility
from .qa_training_service_core import *  # noqa: F401,F403


def _visible_ticket_query(db: Session, user: User):
    query = db.query(Ticket)
    if has_global_case_visibility(user, db):
        return query
    predicates = [Ticket.assignee_id == user.id]
    if user.team_id is not None:
        predicates.append(Ticket.team_id == user.team_id)
    return query.filter(or_(*predicates))


def _with_ticket_visibility(query: Query, user: User):
    db = query.session
    if has_global_case_visibility(user, db):
        return query
    predicates = [Ticket.assignee_id == user.id]
    if user.team_id is not None:
        predicates.append(Ticket.team_id == user.team_id)
    return query.filter(or_(*predicates))


_core._visible_ticket_query = _visible_ticket_query
_core._with_ticket_visibility = _with_ticket_visibility


def __getattr__(name: str):
    return getattr(_core, name)


__all__ = [
    "build_qa_training",
    "submit_agent_appeal",
    "submit_knowledge_gap",
]
