from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..enums import UserRole
from ..identity_schemas import RoleProfileRead, UserSecurityStateRead, UserTeamAssignmentRequest
from ..models import Team, User
from ..models_identity import UserSecurityState
from ..services.audit_service import log_admin_audit
from ..services.permissions import ROLE_CAPABILITIES, ensure_can_manage_users
from ..services.user_security_service import (
    ensure_security_state,
    require_password_change_and_revoke,
    revoke_all_sessions,
)
from ..unit_of_work import managed_session
from .deps import get_current_user

router = APIRouter(prefix='/api/admin', tags=['identity-administration'])


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


@router.get('/roles', response_model=list[RoleProfileRead])
def list_role_profiles(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_users(current_user, db)
    return [
        RoleProfileRead(role=role, capabilities=sorted(ROLE_CAPABILITIES.get(role, set())))
        for role in UserRole
    ]


@router.put('/users/{user_id}/team')
def assign_user_team(
    user_id: int,
    payload: UserTeamAssignmentRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_users(current_user, db)
    target = _user_or_404(db, user_id)
    if payload.team_id is not None:
        team = db.query(Team).filter(Team.id == payload.team_id, Team.is_active.is_(True)).first()
        if team is None:
            raise HTTPException(status_code=404, detail='Team not found or inactive')
    with managed_session(db):
        previous_team_id = target.team_id
        target.team_id = payload.team_id
        db.flush()
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action='user.team.assign',
            target_type='user',
            target_id=target.id,
            old_value={'team_id': previous_team_id},
            new_value={'team_id': target.team_id},
        )
        db.flush()
    return {'ok': True, 'user_id': target.id, 'team_id': target.team_id}


@router.delete('/users/{user_id}/email')
def clear_user_email(
    user_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_users(current_user, db)
    target = _user_or_404(db, user_id)
    with managed_session(db):
        previous_email = target.email
        target.email = None
        db.flush()
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action='user.email.clear',
            target_type='user',
            target_id=target.id,
            old_value={'email': previous_email},
            new_value={'email': None},
        )
        db.flush()
    return {'ok': True, 'user_id': target.id, 'email': None}


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
        previous = ensure_security_state(db, target.id)
        before = {
            'must_change_password': previous.must_change_password,
            'session_version': previous.session_version,
        }
        state = require_password_change_and_revoke(db, target.id)
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
