from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from .permissions import (
    CAP_AUDIT_READ,
    CAP_RUNTIME_MANAGE,
    ensure_can_manage_runtime,
    resolve_capabilities,
)


def ensure_can_read_runtime(user, db: Session | None = None) -> None:
    """Authorize read-only runtime and audit projections.

    Runtime mutation remains governed by ``ensure_can_manage_runtime``.  This
    helper exists so audit-only users can inspect bounded runtime evidence
    without receiving write authority.
    """

    capabilities = resolve_capabilities(user, db)
    if CAP_RUNTIME_MANAGE in capabilities or CAP_AUDIT_READ in capabilities:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Not authorized to read runtime",
    )


__all__ = ["ensure_can_read_runtime", "ensure_can_manage_runtime"]
