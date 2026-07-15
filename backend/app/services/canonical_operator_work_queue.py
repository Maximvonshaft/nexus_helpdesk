"""Canonical unified operator queue authority."""

from sqlalchemy import or_

from ..models import Ticket
from . import operator_work_queue_core as _core
from .permissions import has_global_case_visibility
from .operator_work_queue_core import *  # noqa: F401,F403


def _visibility_filter(query, *, current_user):
    db = query.session
    if has_global_case_visibility(current_user, db):
        return query
    predicates = [Ticket.assignee_id == int(current_user.id)]
    if getattr(current_user, "team_id", None):
        predicates.append(Ticket.team_id == int(current_user.team_id))
    return query.filter(or_(*predicates))


_core._visibility_filter = _visibility_filter


def __getattr__(name: str):
    return getattr(_core, name)


__all__ = ["list_unified_operator_queue"]
