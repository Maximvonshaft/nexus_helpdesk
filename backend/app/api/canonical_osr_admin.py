"""Canonical OSR administration router.

The retained route definitions live in ``osr_admin_core``. This module replaces
the historical role-name authorization hook with the runtime capability policy
before the router is exposed to the application.
"""

from sqlalchemy.orm import Session

from ..services.permissions import ensure_can_manage_runtime
from . import osr_admin_core as _core


def _ensure_osr_admin(current_user, db: Session) -> None:
    ensure_can_manage_runtime(current_user, db)


_core._ensure_osr_admin = _ensure_osr_admin
router = _core.router


def __getattr__(name: str):
    return getattr(_core, name)


__all__ = ["router"]
