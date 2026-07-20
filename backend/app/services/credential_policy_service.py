from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from ..models import User
from ..models_identity_policy import UserCredentialPolicy
from ..utils.time import utc_now


def get_credential_policy(db: Session, user_id: int) -> UserCredentialPolicy | None:
    return db.get(UserCredentialPolicy, user_id)


def ensure_credential_policy(
    db: Session,
    user_id: int,
    *,
    must_change_password: bool = False,
) -> UserCredentialPolicy:
    row = get_credential_policy(db, user_id)
    if row is not None:
        return row
    row = UserCredentialPolicy(
        user_id=user_id,
        must_change_password=must_change_password,
    )
    db.add(row)
    db.flush()
    return row


def password_change_required(db: Session, user_id: int) -> bool:
    row = get_credential_policy(db, user_id)
    return bool(row.must_change_password) if row is not None else False


def record_successful_login(db: Session, user_id: int) -> UserCredentialPolicy:
    row = ensure_credential_policy(db, user_id)
    now = utc_now()
    row.last_login_at = now
    row.updated_at = now
    db.flush()
    return row


def complete_password_change(db: Session, user_id: int) -> UserCredentialPolicy:
    row = ensure_credential_policy(db, user_id)
    db.refresh(row)
    now = utc_now()
    row.must_change_password = False
    row.password_changed_at = now
    row.updated_at = now
    db.flush()
    return row


def require_password_change(db: Session, user_id: int) -> UserCredentialPolicy:
    row = ensure_credential_policy(db, user_id)
    now = utc_now()
    row.must_change_password = True
    row.updated_at = now
    db.flush()
    return row


def advance_user_identity_version(user: User) -> None:
    """Advance the sole JWT freshness authority monotonically."""

    now = utc_now()
    current = user.updated_at
    if current is not None and now <= current:
        now = current + timedelta(microseconds=1)
    user.updated_at = now


def credential_policy_payload(db: Session, user_id: int) -> dict:
    row = get_credential_policy(db, user_id)
    return {
        "user_id": user_id,
        "must_change_password": bool(row.must_change_password) if row is not None else False,
        "password_changed_at": row.password_changed_at if row is not None else None,
        "last_login_at": row.last_login_at if row is not None else None,
        "updated_at": row.updated_at if row is not None else None,
    }
