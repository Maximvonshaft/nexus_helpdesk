from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from ..auth_service import decode_access_token
from ..db import get_db
from ..enums import UserRole
from ..models import User
from ..settings import get_settings

bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    x_user_id: int | None = Header(default=None, alias="X-User-Id"),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    user = None
    if credentials:
        user_id = decode_access_token(credentials.credentials)
        if user_id:
            user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()

    if user is None and settings.allow_dev_auth and x_user_id:
        user = db.query(User).filter(User.id == x_user_id, User.is_active.is_(True)).first()

    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return user


def require_admin_user(current_user = Depends(get_current_user)):
    if current_user.role != UserRole.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user
