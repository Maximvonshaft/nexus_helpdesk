"""Canonical WebChat handoff service.

Handoff queue visibility is derived from effective capabilities and ticket
ownership. Role names remain only in the central capability-default mapping.
"""

from ..models import Ticket, User
from . import webchat_handoff_service_core as _core
from .permissions import (
    CAP_AUDIT_READ,
    CAP_TICKET_ASSIGN,
    CAP_USER_MANAGE,
    CAP_WEBCHAT_HANDOFF_FORCE_TAKEOVER,
)
from .webchat_handoff_service_core import *  # noqa: F401,F403

_GLOBAL_CASE_VISIBILITY = frozenset({CAP_TICKET_ASSIGN, CAP_AUDIT_READ, CAP_USER_MANAGE})


def _visible_from_preloaded(user: User, ticket: Ticket, capabilities: set[str]) -> bool:
    if capabilities & _GLOBAL_CASE_VISIBILITY:
        return True
    if ticket.assignee_id == user.id:
        return True
    if user.team_id and ticket.team_id == user.team_id:
        return True
    return CAP_WEBCHAT_HANDOFF_FORCE_TAKEOVER in capabilities


_core._visible_from_preloaded = _visible_from_preloaded


def __getattr__(name: str):
    return getattr(_core, name)
