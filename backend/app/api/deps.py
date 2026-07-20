from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from ..auth_service import load_authenticated_user_for_token
from ..db import get_db
from ..models import User
from ..services.credential_policy_service import password_change_required
from ..services.permissions import CAP_USER_MANAGE, resolve_capabilities
from ..settings import get_settings

bearer = HTTPBearer(auto_error=False)


def _resolve_authenticated_user(
    *,
    credentials: HTTPAuthorizationCredentials | None,
    x_user_id: int | None,
    db: Session,
) -> User:
    settings = get_settings()
    user = load_authenticated_user_for_token(db, credentials.credentials) if credentials else None

    if user is None and settings.allow_dev_auth and x_user_id:
        user = db.query(User).filter(User.id == x_user_id, User.is_active.is_(True)).first()

    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return user


def get_authenticated_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    x_user_id: int | None = Header(default=None, alias="X-User-Id"),
    db: Session = Depends(get_db),
):
    """Authenticate a fresh active identity, including password-recovery access."""

    return _resolve_authenticated_user(
        credentials=credentials,
        x_user_id=x_user_id,
        db=db,
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    x_user_id: int | None = Header(default=None, alias="X-User-Id"),
    db: Session = Depends(get_db),
):
    """Authorize normal application access after credential policy checks.

    The signature intentionally preserves the repository's canonical direct-call
    contract for tests and internal adapters while recovery endpoints depend on
    ``get_authenticated_user`` explicitly.
    """

    current_user = _resolve_authenticated_user(
        credentials=credentials,
        x_user_id=x_user_id,
        db=db,
    )
    if password_change_required(db, current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Password change required",
        )
    return current_user


def require_admin_user(
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if CAP_USER_MANAGE not in resolve_capabilities(current_user, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User management capability required")
    return current_user
