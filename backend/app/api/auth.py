from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..auth_service import create_access_token, verify_password
from ..db import get_db
from ..models import User
from ..schemas import AuthUserRead, LoginRequest, LoginResponse
from ..services.auth_throttle import build_login_throttle_key, clear_login_failures, enforce_login_allowed, record_login_failure
from ..utils.client_ip import get_client_ip
from ..unit_of_work import managed_session
from .deps import get_current_user
from ..services.permissions import resolve_capabilities

router = APIRouter(prefix='/api/auth', tags=['auth'])


@router.post('/login', response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    throttle_key = build_login_throttle_key(payload.username, get_client_ip(request))
    enforce_login_allowed(db, throttle_key)
    user = db.query(User).filter(User.username == payload.username, User.is_active.is_(True)).first()
    if not user or not verify_password(payload.password, user.password_hash):
        with managed_session(db):
            record_login_failure(db, throttle_key)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid credentials')
    with managed_session(db):
        clear_login_failures(db, throttle_key)
        db.flush()
    token = create_access_token(user.id)
    return LoginResponse(
        access_token=token, 
        user=AuthUserRead(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            email=user.email,
            role=user.role,
            team_id=user.team_id,
            capabilities=sorted(resolve_capabilities(user, db))
        )
    )


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
