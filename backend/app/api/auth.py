from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth_service import create_access_token, hash_password, verify_password
from ..db import get_db
from ..identity_schemas import (
    AccountSecurityRead,
    AuthSessionResponse,
    AuthSessionUserRead,
    ChangePasswordRequest,
)
from ..models import User
from ..schemas import LoginRequest
from ..services.audit_service import log_admin_audit
from ..services.auth_throttle import build_login_throttle_key, clear_login_failures, enforce_login_allowed, record_login_failure
from ..services.password_policy import PasswordPolicyError, validate_admin_password_policy
from ..services.permissions import resolve_capabilities
from ..services.user_security_service import (
    complete_password_change,
    record_successful_login,
    revoke_all_sessions,
    security_state_payload,
    session_version_for_user,
)
from ..unit_of_work import managed_session
from ..utils.client_ip import get_client_ip
from .deps import get_current_user

router = APIRouter(prefix='/api/auth', tags=['auth'])


def _validate_password(password: str) -> None:
    try:
        validate_admin_password_policy(password)
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _auth_user_for(user: User, db: Session) -> AuthSessionUserRead:
    security = security_state_payload(db, user.id)
    return AuthSessionUserRead.model_validate(user).model_copy(
        update={
            'capabilities': sorted(resolve_capabilities(user, db)),
            'must_change_password': security['must_change_password'],
            'password_changed_at': security['password_changed_at'],
            'last_login_at': security['last_login_at'],
        }
    )


def _login_response_for_user(user: User, db: Session) -> AuthSessionResponse:
    session_version = session_version_for_user(db, user.id)
    return AuthSessionResponse(
        access_token=create_access_token(user.id, session_version=session_version),
        user=_auth_user_for(user, db),
    )


@router.post('/login', response_model=AuthSessionResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    username = payload.username.strip()
    throttle_key = build_login_throttle_key(username, get_client_ip(request))
    enforce_login_allowed(db, throttle_key)
    user = db.query(User).filter(func.lower(User.username) == username.lower(), User.is_active.is_(True)).first()
    if not user or not verify_password(payload.password, user.password_hash):
        with managed_session(db):
            record_login_failure(db, throttle_key)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid credentials')
    with managed_session(db):
        clear_login_failures(db, throttle_key)
        record_successful_login(db, user.id)
        db.flush()
    return _login_response_for_user(user, db)


@router.get('/me', response_model=AuthSessionUserRead)
def me(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _auth_user_for(current_user, db)


@router.get('/security', response_model=AccountSecurityRead)
def account_security(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return AccountSecurityRead(**security_state_payload(db, current_user.id))


@router.post('/change-password', response_model=AuthSessionResponse)
def change_password(
    payload: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(payload.current_password, current_user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Current password is incorrect')
    _validate_password(payload.new_password)
    if verify_password(payload.new_password, current_user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='New password must be different')

    with managed_session(db):
        current_user.password_hash = hash_password(payload.new_password)
        db.flush()  # Rotates the canonical session version through the model authority.
        state = complete_password_change(db, current_user.id)
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action='user.change_password',
            target_type='user',
            target_id=current_user.id,
            old_value={},
            new_value={'session_version': state.session_version},
        )
        db.flush()
    db.expire_all()
    return _login_response_for_user(current_user, db)


@router.post('/logout-all')
def logout_all_sessions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    with managed_session(db):
        state = revoke_all_sessions(db, current_user.id)
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action='user.logout_all',
            target_type='user',
            target_id=current_user.id,
            old_value={},
            new_value={'session_version': state.session_version},
        )
        db.flush()
    return {'ok': True}
