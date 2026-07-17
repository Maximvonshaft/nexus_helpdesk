from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from ..db import SessionLocal
from .audit_service import log_admin_audit
from .permissions import CAP_CUSTOMER_PROFILE_READ, resolve_capabilities


_AUDIT_SESSION_KEY = "support_sensitive_access_prechecked"
_TICKET_PATH = re.compile(
    r"^/api/webchat/admin/tickets/(?P<ticket_id>[1-9]\d*)/(?P<surface>thread|support-memory)$"
)


@dataclass(frozen=True)
class SensitiveSupportSurface:
    name: str
    target_id: int | None = None
    target_reference_hash: str | None = None


def classify_sensitive_support_request(request: Request) -> SensitiveSupportSurface | None:
    if request.method.upper() != "GET":
        return None

    path = request.url.path.rstrip("/") or "/"
    if path == "/api/support/conversations/detail":
        session_key = str(request.query_params.get("session_key") or "").strip()
        reference_hash = (
            hashlib.sha256(session_key.encode("utf-8")).hexdigest()[:16]
            if session_key
            else None
        )
        return SensitiveSupportSurface(
            "support_conversation_detail",
            target_reference_hash=reference_hash,
        )

    match = _TICKET_PATH.fullmatch(path)
    if match:
        return SensitiveSupportSurface(
            f"webchat_{match.group('surface').replace('-', '_')}",
            int(match.group("ticket_id")),
        )
    return None


def enforce_sensitive_support_request(
    request: Request,
    *,
    db: Session,
    current_user,
) -> None:
    """Require sensitive-read capability and persist a bounded precheck event.

    Object existence, tenant authority and ticket visibility remain fail-closed in
    the endpoint's canonical query/service boundary. This precheck deliberately
    does not claim that object-level authorization or data disclosure succeeded.
    """

    surface = classify_sensitive_support_request(request)
    if surface is None:
        return

    if CAP_CUSTOMER_PROFILE_READ not in resolve_capabilities(current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="support_sensitive_read_requires_customer_profile_capability",
        )

    audit_key = (
        int(current_user.id),
        surface.name,
        surface.target_id,
        surface.target_reference_hash,
    )
    prechecked = db.info.setdefault(_AUDIT_SESSION_KEY, set())
    if audit_key in prechecked:
        return

    audit_db = SessionLocal()
    try:
        log_admin_audit(
            audit_db,
            actor_id=int(current_user.id),
            action="support_sensitive_read_capability_precheck",
            target_type="support_conversation",
            target_id=surface.target_id,
            new_value={
                "surface": surface.name,
                "method": "GET",
                "capability": CAP_CUSTOMER_PROFILE_READ,
                "authorization_stage": "capability_precheck",
                "object_scope_enforced_by_endpoint": True,
                "access_outcome": "pending_object_scope",
                "target_reference_hash": surface.target_reference_hash,
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

    prechecked.add(audit_key)
