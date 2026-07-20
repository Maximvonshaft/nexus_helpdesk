from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth_service import create_access_token, hash_password, verify_password
from ..db import get_db
from ..models import User
from ..services.audit_service import log_admin_audit
from ..services.auth_throttle import build_login_throttle_key, clear_login_failures, enforce_login_allowed, record_login_failure
from ..services.password_policy import PasswordPolicyError, validate_admin_password_policy
from ..services.permissions import resolve_capabilities
from ..services.user_security_service import (
    complete_password_change,
    get_security_state,
    record_successful_login,
    revoke_all_sessions,
    security_state_payload,
    session_version_for_user,
)
from ..unit_of_work import managed_session
from ..utils.client_ip import get_client_ip
from .deps import get_current_user

router = APIRouter(prefix='/api/auth', tags=['auth'])


class LoginRequest(BaseModel):
    username: str
    password: str


class AuthUserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str
    email: str | None = None
    role: str
    team_id: int | None = None
    capabilities: list[str] = Field(default_factory=list)
    must_change_password: bool = False
    password_changed_at: datetime | None = None
    last_login_at: datetime | None = None


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = 'bearer'
    user: AuthUserRead


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class AccountSecurityRead(BaseModel):
    user_id: int
    session_version: int
    must_change_password: bool
    password_changed_at: datetime | None = None
    last_login_at: datetime | None = None
    updated_at: datetime | None = None


def _validate_password(password: str) -> None:
    try:
        validate_admin_password_policy(password)
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _auth_user_for(user: User, db: Session) -> AuthUserRead:
    security = security_state_payload(db, user.id)
    role = user.role.value if hasattr(user.role, 'value') else str(user.role)
    return AuthUserRead(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        role=role,
        team_id=user.team_id,
        capabilities=sorted(resolve_capabilities(user, db)),
        must_change_password=security['must_change_password'],
        password_changed_at=security['password_changed_at'],
        last_login_at=security['last_login_at'],
    )


def _login_response_for_user(user: User, db: Session) -> LoginResponse:
    session_version = session_version_for_user(db, user.id)
    return LoginResponse(
        access_token=create_access_token(user.id, session_version=session_version),
        user=_auth_user_for(user, db),
    )


@router.post('/login', response_model=LoginResponse)
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


@router.get('/me', response_model=AuthUserRead)
def me(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _auth_user_for(current_user, db)


@router.get('/security', response_model=AccountSecurityRead)
def account_security(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return AccountSecurityRead(**security_state_payload(db, current_user.id))


@router.post('/change-password', response_model=LoginResponse)
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
