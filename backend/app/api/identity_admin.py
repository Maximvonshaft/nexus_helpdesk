from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..models_identity import UserSecurityState
from ..services.audit_service import log_admin_audit
from ..services.permissions import ensure_can_manage_users
from ..services.user_security_service import ensure_security_state, revoke_all_sessions
from ..unit_of_work import managed_session
from ..utils.time import utc_now
from .deps import get_current_user

router = APIRouter(prefix='/api/admin', tags=['identity-administration'])


class UserSecurityStateRead(BaseModel):
    user_id: int
    session_version: int
    must_change_password: bool
    password_changed_at: datetime | None = None
    last_login_at: datetime | None = None
    updated_at: datetime | None = None


def _user_or_404(db: Session, user_id: int) -> User:
    row = db.query(User).filter(User.id == user_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail='User not found')
    return row


def _serialize(row: UserSecurityState) -> UserSecurityStateRead:
    return UserSecurityStateRead(
        user_id=row.user_id,
        session_version=max(1, int(row.session_version)),
        must_change_password=bool(row.must_change_password),
        password_changed_at=row.password_changed_at,
        last_login_at=row.last_login_at,
        updated_at=row.updated_at,
    )


@router.get('/user-security-states', response_model=list[UserSecurityStateRead])
def list_user_security_states(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_users(current_user, db)
    users = db.query(User.id).order_by(User.id.asc()).all()
    states = {
        row.user_id: row
        for row in db.query(UserSecurityState).order_by(UserSecurityState.user_id.asc()).all()
    }
    return [
        _serialize(states[user_id])
        if user_id in states
        else UserSecurityStateRead(
            user_id=user_id,
            session_version=1,
            must_change_password=False,
        )
        for (user_id,) in users
    ]


@router.post('/users/{user_id}/logout-all', response_model=UserSecurityStateRead)
def admin_logout_all_sessions(
    user_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_users(current_user, db)
    target = _user_or_404(db, user_id)
    with managed_session(db):
        state = revoke_all_sessions(db, target.id)
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action='user.admin_logout_all',
            target_type='user',
            target_id=target.id,
            old_value={},
            new_value={'session_version': state.session_version},
        )
        db.flush()
    return _serialize(state)


@router.post('/users/{user_id}/require-password-change', response_model=UserSecurityStateRead)
def require_password_change(
    user_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_users(current_user, db)
    target = _user_or_404(db, user_id)
    with managed_session(db):
        state = ensure_security_state(db, target.id)
        before = {
            'must_change_password': state.must_change_password,
            'session_version': state.session_version,
        }
        state.must_change_password = True
        state.session_version = max(1, int(state.session_version)) + 1
        state.updated_at = utc_now()
        db.flush()
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action='user.require_password_change',
            target_type='user',
            target_id=target.id,
            old_value=before,
            new_value={
                'must_change_password': True,
                'session_version': state.session_version,
            },
        )
        db.flush()
    return _serialize(state)
