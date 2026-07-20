from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.audit_service import log_admin_audit
from ..services.credential_policy_service import advance_user_identity_version, ensure_credential_policy
from ..services.identity_tenant_scope import actor_tenant_id, user_for_actor
from ..services.mfa_service import clear_mfa, mfa_status_payload
from ..services.permissions import ensure_can_manage_users
from ..unit_of_work import managed_session
from .deps import get_current_user

router = APIRouter(prefix="/api/admin/identity", tags=["admin-identity"])


class AdminMfaResetRead(BaseModel):
    ok: bool
    user_id: int
    sessions_revoked: bool


@router.post("/users/{user_id}/reset-mfa", response_model=AdminMfaResetRead)
def reset_user_mfa(
    user_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_users(current_user, db)
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Use account security to change your own MFA",
        )
    tenant_id = actor_tenant_id(db, current_user)
    target = user_for_actor(db, tenant_id, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    policy = ensure_credential_policy(db, target.id)
    before = mfa_status_payload(policy)
    if not before["enabled"] and not before["setup_pending"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MFA is not configured")

    with managed_session(db):
        clear_mfa(db, target.id)
        advance_user_identity_version(target)
        db.flush()
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action="user.mfa_reset",
            target_type="user",
            target_id=target.id,
            old_value={
                "mfa_enabled": before["enabled"],
                "setup_pending": before["setup_pending"],
                "recovery_codes_remaining": before["recovery_codes_remaining"],
            },
            new_value={
                "mfa_enabled": False,
                "setup_pending": False,
                "recovery_codes_remaining": 0,
                "sessions_revoked": True,
            },
        )
        db.flush()
    return AdminMfaResetRead(ok=True, user_id=target.id, sessions_revoked=True)
