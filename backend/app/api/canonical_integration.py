"""Canonical integration API router.

Integration-created cases are assigned only to an active user whose effective
policy includes ``ticket.assign``. Role names are not used as runtime authority,
and the system never falls back to an arbitrary active account.
"""

from sqlalchemy.orm import Session

from ..models import User
from ..services.permissions import CAP_TICKET_ASSIGN, resolve_capabilities
from . import integration_core as _core


def _pick_actor(db: Session) -> User:
    candidates = db.query(User).filter(User.is_active.is_(True)).order_by(User.id.asc()).all()
    for actor in candidates:
        if CAP_TICKET_ASSIGN in resolve_capabilities(actor, db):
            return actor
    raise RuntimeError("No active user with ticket.assign is available for integration-created tickets")


_core._pick_actor = _pick_actor
router = _core.router


def __getattr__(name: str):
    return getattr(_core, name)


__all__ = ["router"]
