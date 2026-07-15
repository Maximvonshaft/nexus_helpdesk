from __future__ import annotations

from sqlalchemy.orm import Session

from .permissions import (
    CAP_AUDIT_READ,
    CAP_TICKET_ASSIGN,
    CAP_USER_MANAGE,
    resolve_capabilities,
)

_GLOBAL_CASE_VISIBILITY = frozenset({CAP_TICKET_ASSIGN, CAP_AUDIT_READ, CAP_USER_MANAGE})
_GLOBAL_ADMIN_VISIBILITY = frozenset({CAP_AUDIT_READ, CAP_USER_MANAGE})


def has_global_case_visibility(user, db: Session | None = None) -> bool:
    """Return whether the actor may inspect cases outside their own team scope."""

    return bool(resolve_capabilities(user, db) & _GLOBAL_CASE_VISIBILITY)


def has_global_admin_visibility(user, db: Session | None = None) -> bool:
    """Return whether the actor may inspect global administrative projections."""

    return bool(resolve_capabilities(user, db) & _GLOBAL_ADMIN_VISIBILITY)
