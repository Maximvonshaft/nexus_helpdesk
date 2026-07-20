from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth_service import create_access_token, hash_password, verify_password
from ..db import get_db
from ..models import User
from ..schemas import AuthUserRead, LoginRequest, LoginResponse
from ..services.audit_service import log_admin_audit
from ..services.auth_throttle import build_login_throttle_key, clear_login_failures, enforce_login_allowed, record_login_failure
from ..services.password_policy import PasswordPolicyError, validate_admin_password_policy
from ..services.permissions import capability_fingerprint, resolve_capabilities
from ..utils.client_ip import get_client_ip
from ..utils.time import utc_now
from ..unit_of_work import managed_session
from .deps import get_current_user

router = APIRouter(prefix='/api/auth', tags=['auth'])


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=4096)
    new_password: str = Field(min_length=1, max_length=4096)


class PasswordChangeResponse(BaseModel):
    ok: bool
    reauthenticate: bool


def _login_response_for_user(user: User, db: Session) -> LoginResponse:
    capabilities = sorted(resolve_capabilities(user, db))
    token = create_access_token(
        user.id,
        user.updated_at,
        policy_fingerprint=capability_fingerprint(user, db),
    )
    return LoginResponse(
        access_token=token,
        user=AuthUserRead(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            email=user.email,
            role=user.role,
            team_id=user.team_id,
            capabilities=capabilities,
        )
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
        db.flush()
    return _login_response_for_user(user, db)


@router.get('/me', response_model=AuthUserRead)
def me(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return AuthUserRead(
        id=current_user.id,
        username=current_user.username,
        display_name=current_user.display_name,
        email=current_user.email,
        role=current_user.role,
        team_id=current_user.team_id,
        capabilities=sorted(resolve_capabilities(current_user, db))
    )


@router.post('/change-password', response_model=PasswordChangeResponse)
def change_password(
    payload: PasswordChangeRequest,
    current_user: User = Depends(get_current_user),
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
        current_user.updated_at = utc_now()
        db.flush()
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action='auth.password_changed',
            target_type='user',
            target_id=current_user.id,
            old_value=None,
            new_value={'reauthentication_required': True},
        )
    return PasswordChangeResponse(ok=True, reauthenticate=True)
