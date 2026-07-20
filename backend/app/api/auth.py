from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth_service import create_access_token, hash_password, verify_password
from ..db import get_db
from ..identity_schemas import (
    AuthSessionResponse,
    AuthSessionUserRead,
    PasswordChangeRequest,
    PasswordChangeResponse,
)
from ..models import User
from ..schemas import LoginRequest
from ..services.audit_service import log_admin_audit
from ..services.auth_throttle import build_login_throttle_key, clear_login_failures, enforce_login_allowed, record_login_failure
from ..services.credential_policy_service import (
    advance_user_identity_version,
    complete_password_change,
    credential_policy_payload,
    record_successful_login,
)
from ..services.password_policy import PasswordPolicyError, validate_admin_password_policy
from ..services.permissions import capability_fingerprint, resolve_capabilities
from ..unit_of_work import managed_session
from ..utils.client_ip import get_client_ip
from .deps import get_authenticated_user

router = APIRouter(prefix='/api/auth', tags=['auth'])


def _session_user_for(user: User, db: Session) -> AuthSessionUserRead:
    policy = credential_policy_payload(db, user.id)
    return AuthSessionUserRead(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        role=user.role,
        team_id=user.team_id,
        capabilities=sorted(resolve_capabilities(user, db)),
        must_change_password=policy['must_change_password'],
        password_changed_at=policy['password_changed_at'],
        last_login_at=policy['last_login_at'],
    )


def _login_response_for_user(user: User, db: Session) -> AuthSessionResponse:
    token = create_access_token(
        user.id,
        user.updated_at,
        policy_fingerprint=capability_fingerprint(user, db),
    )
    return AuthSessionResponse(
        access_token=token,
        user=_session_user_for(user, db),
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
def me(current_user: User = Depends(get_authenticated_user), db: Session = Depends(get_db)):
    return _session_user_for(current_user, db)


@router.post('/change-password', response_model=PasswordChangeResponse)
def change_password(
    payload: PasswordChangeRequest,
    current_user: User = Depends(get_authenticated_user),
    db: Session = Depends(get_db),
):
    if not verify_password(payload.current_password, current_user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Current password is incorrect')
    if verify_password(payload.new_password, current_user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='New password must be different')
    try:
        validate_admin_password_policy(payload.new_password)
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    with managed_session(db):
        current_user.password_hash = hash_password(payload.new_password)
        advance_user_identity_version(current_user)
        db.flush()
        complete_password_change(db, current_user.id)
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action='auth.password_changed',
            target_type='user',
            target_id=current_user.id,
            old_value=None,
            new_value={'reauthentication_required': True},
        )
        db.flush()
    return PasswordChangeResponse(ok=True, reauthenticate=True)


@router.post('/logout-all')
def logout_all_sessions(
    current_user: User = Depends(get_authenticated_user),
    db: Session = Depends(get_db),
):
    with managed_session(db):
        advance_user_identity_version(current_user)
        db.flush()
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action='auth.sessions_revoked',
            target_type='user',
            target_id=current_user.id,
            old_value=None,
            new_value={'all_sessions_revoked': True},
        )
        db.flush()
    return {'ok': True}
