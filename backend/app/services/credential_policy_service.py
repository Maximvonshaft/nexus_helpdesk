from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..models import User
from ..models_identity_policy import UserCredentialPolicy
from ..utils.time import utc_now


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


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
    """Advance the sole JWT freshness authority monotonically.

    PostgreSQL returns timezone-aware values while SQLite may return a naive
    value for the same ``DateTime(timezone=True)`` column. Normalize both sides
    to UTC before comparison so credential and MFA changes remain portable.
    """

    now = _utc(utc_now())
    current = _utc(user.updated_at) if user.updated_at is not None else None
    if current is not None and now <= current:
        now = current + timedelta(microseconds=1)
    user.updated_at = now


def credential_policy_payload(db: Session, user_id: int) -> dict:
    row = get_credential_policy(db, user_id)
    recovery_codes_remaining = 0
    if row is not None and row.mfa_recovery_codes_json:
        try:
            import json

            value = json.loads(row.mfa_recovery_codes_json)
            recovery_codes_remaining = len(value) if isinstance(value, list) else 0
        except (TypeError, json.JSONDecodeError):
            recovery_codes_remaining = 0
    return {
        "user_id": user_id,
        "must_change_password": bool(row.must_change_password) if row is not None else False,
        "password_changed_at": row.password_changed_at if row is not None else None,
        "last_login_at": row.last_login_at if row is not None else None,
        "mfa_enabled": bool(row.mfa_enabled) if row is not None else False,
        "mfa_confirmed_at": row.mfa_confirmed_at if row is not None else None,
        "mfa_last_verified_at": row.mfa_last_verified_at if row is not None else None,
        "mfa_recovery_codes_remaining": recovery_codes_remaining,
        "updated_at": row.updated_at if row is not None else None,
    }
