from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from ..auth_service import decode_access_token_claims
from ..db import get_db
from ..models import User
from ..services.permissions import CAP_USER_MANAGE, resolve_capabilities
from ..services.user_security_service import get_security_state
from ..settings import get_settings

bearer = HTTPBearer(auto_error=False)

PASSWORD_ROTATION_ALLOWED_PATHS = frozenset({
    "/api/auth/me",
    "/api/auth/change-password",
    "/api/auth/logout-all",
})


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    x_user_id: int | None = Header(default=None, alias="X-User-Id"),
    db: Session = Depends(get_db),
    request: Request = None,
):
    settings = get_settings()
    user = None
    security_state = None
    if credentials:
        claims = decode_access_token_claims(credentials.credentials)
        if claims:
            user = db.query(User).filter(User.id == claims.user_id, User.is_active.is_(True)).first()
            if user is not None:
                security_state = get_security_state(db, user.id)
                expected_version = security_state.session_version if security_state is not None else 1
                if claims.session_version != expected_version:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Session expired",
                    )

    if user is None and settings.allow_dev_auth and x_user_id:
        user = db.query(User).filter(User.id == x_user_id, User.is_active.is_(True)).first()
        if user is not None:
            security_state = get_security_state(db, user.id)

    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    request_path = request.url.path if request is not None else ""
    if (
        security_state is not None
        and security_state.must_change_password
        and request_path not in PASSWORD_ROTATION_ALLOWED_PATHS
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Password change required",
        )
    return user


def require_admin_user(
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if CAP_USER_MANAGE not in resolve_capabilities(current_user, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User management capability required")
    return current_user
