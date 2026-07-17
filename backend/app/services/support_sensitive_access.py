from __future__ import annotations

from typing import Literal

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..db import SessionLocal
from .audit_service import log_admin_audit
from .permissions import CAP_CUSTOMER_PROFILE_READ, ensure_capability


SensitiveSupportSurface = Literal["webchat_thread"]
_ALLOWED_SURFACES: frozenset[str] = frozenset({"webchat_thread"})


def ensure_sensitive_support_capability(db: Session, current_user) -> None:
    """Require the single capability that permits full customer-detail reads."""

    ensure_capability(
        current_user,
        CAP_CUSTOMER_PROFILE_READ,
        db,
        message="support_sensitive_read_requires_customer_profile_capability",
    )


def audit_sensitive_support_read(
    *,
    current_user,
    ticket_id: int,
    surface: SensitiveSupportSurface,
    includes_support_memory: bool,
) -> None:
    """Persist bounded evidence only after object-scope authorization succeeds."""

    if surface not in _ALLOWED_SURFACES:
        raise ValueError("unsupported_sensitive_support_surface")
    target_id = int(ticket_id)
    if target_id <= 0:
        raise ValueError("invalid_sensitive_support_target")

    audit_db = SessionLocal()
    try:
        log_admin_audit(
            audit_db,
            actor_id=int(current_user.id),
            action="support_sensitive_read_authorized",
            target_type="support_conversation",
            target_id=target_id,
            new_value={
                "surface": surface,
                "method": "GET",
                "capability": CAP_CUSTOMER_PROFILE_READ,
                "authorization_stage": "object_scope_completed",
                "access_outcome": "authorized",
                "includes_support_memory": bool(includes_support_memory),
                "pii_payload_logged": False,
            },
        )
        audit_db.commit()
    except Exception as exc:
        audit_db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="support_sensitive_read_audit_unavailable",
        ) from exc
    finally:
        audit_db.close()
