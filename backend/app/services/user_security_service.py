from __future__ import annotations

from sqlalchemy.orm import Session

from ..models_identity import UserSecurityState
from ..utils.time import utc_now


def get_security_state(db: Session, user_id: int) -> UserSecurityState | None:
    return db.get(UserSecurityState, user_id)


def ensure_security_state(
    db: Session,
    user_id: int,
    *,
    must_change_password: bool = False,
) -> UserSecurityState:
    row = get_security_state(db, user_id)
    if row is not None:
        return row
    row = UserSecurityState(
        user_id=user_id,
        session_version=1,
        must_change_password=must_change_password,
    )
    db.add(row)
    db.flush()
    return row


def session_version_for_user(db: Session, user_id: int) -> int:
    row = get_security_state(db, user_id)
    return max(1, int(row.session_version)) if row is not None else 1


def record_successful_login(db: Session, user_id: int) -> UserSecurityState:
    row = ensure_security_state(db, user_id)
    row.last_login_at = utc_now()
    row.updated_at = utc_now()
    db.flush()
    return row


def complete_password_change(db: Session, user_id: int) -> UserSecurityState:
    row = ensure_security_state(db, user_id)
    # Password changes rotate the version through the User model event using a
    # connection-level UPDATE. Refresh before clearing the forced-change flag so
    # the ORM state and the audit record observe the same canonical version.
    db.refresh(row)
    row.must_change_password = False
    row.updated_at = utc_now()
    db.flush()
    return row


def revoke_all_sessions(db: Session, user_id: int) -> UserSecurityState:
    row = ensure_security_state(db, user_id)
    row.session_version = max(1, int(row.session_version)) + 1
    row.updated_at = utc_now()
    db.flush()
    return row


def security_state_payload(db: Session, user_id: int) -> dict:
    row = get_security_state(db, user_id)
    return {
        "user_id": user_id,
        "session_version": max(1, int(row.session_version)) if row is not None else 1,
        "must_change_password": bool(row.must_change_password) if row is not None else False,
        "password_changed_at": row.password_changed_at if row is not None else None,
        "last_login_at": row.last_login_at if row is not None else None,
        "updated_at": row.updated_at if row is not None else None,
    }
