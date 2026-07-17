from __future__ import annotations

import re
from dataclasses import dataclass

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from ..db import SessionLocal
from .audit_service import log_admin_audit
from .permissions import CAP_CUSTOMER_PROFILE_READ, resolve_capabilities


_AUDIT_SESSION_KEY = "support_sensitive_access_audited"
_TICKET_PATH = re.compile(
    r"^/api/webchat/admin/tickets/(?P<ticket_id>[1-9]\d*)/(?P<surface>thread|support-memory)$"
)


@dataclass(frozen=True)
class SensitiveSupportSurface:
    name: str
    target_id: int | None = None


def classify_sensitive_support_request(request: Request) -> SensitiveSupportSurface | None:
    if request.method.upper() != "GET":
        return None

    path = request.url.path.rstrip("/") or "/"
    if path == "/api/support/conversations/detail":
        return SensitiveSupportSurface("support_conversation_detail")

    match = _TICKET_PATH.fullmatch(path)
    if match:
        return SensitiveSupportSurface(
            f"legacy_webchat_{match.group('surface').replace('-', '_')}",
            int(match.group("ticket_id")),
        )
    return None


def enforce_sensitive_support_request(
    request: Request,
    *,
    db: Session,
    current_user,
) -> None:
    surface = classify_sensitive_support_request(request)
    if surface is None:
        return

    if CAP_CUSTOMER_PROFILE_READ not in resolve_capabilities(current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="support_sensitive_read_requires_customer_profile_capability",
        )

    audit_key = (int(current_user.id), surface.name, surface.target_id)
    audited = db.info.setdefault(_AUDIT_SESSION_KEY, set())
    if audit_key in audited:
        return

    audit_db = SessionLocal()
    try:
        log_admin_audit(
            audit_db,
            actor_id=int(current_user.id),
            action="support_sensitive_read_authorized",
            target_type="support_conversation",
            target_id=surface.target_id,
            new_value={
                "surface": surface.name,
                "method": "GET",
                "capability": CAP_CUSTOMER_PROFILE_READ,
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

    audited.add(audit_key)
