from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/identity_control_plane_tests.db")
os.environ.setdefault("JWT_SECRET_KEY", "identity-control-plane-test-secret-that-is-long-enough")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.api.admin import reset_user_password  # noqa: E402
from app.api.auth import ChangePasswordRequest, change_password  # noqa: E402
from app.api.deps import get_current_user  # noqa: E402
from app.api.identity_admin import admin_logout_all_sessions  # noqa: E402
from app.auth_service import create_access_token, verify_password  # noqa: E402
from app.db import Base  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.model_registry import register_all_models  # noqa: E402
from app.models import User  # noqa: E402
from app.models_identity import UserSecurityState  # noqa: E402
from app.schemas import PasswordResetRequest  # noqa: E402
from app.auth_service import hash_password  # noqa: E402

register_all_models()


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "identity.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False}, future=True)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def make_user(db, username: str, role: UserRole, password: str = "Start-Password-123!") -> User:
    row = User(
        username=username,
        display_name=username,
        email=f"{username}@invalid.test",
        password_hash=hash_password(password),
        role=role,
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def test_new_user_receives_one_canonical_forced_rotation_state(db_session):
    user = make_user(db_session, "new-agent", UserRole.agent)
    state = db_session.get(UserSecurityState, user.id)

    assert state is not None
    assert state.session_version == 1
    assert state.must_change_password is True


def test_self_password_change_rotates_session_and_clears_forced_state(db_session):
    user = make_user(db_session, "agent", UserRole.agent)
    state = db_session.get(UserSecurityState, user.id)
    assert state is not None
    old_token = create_access_token(user.id, session_version=state.session_version)

    response = change_password(
        ChangePasswordRequest(
            current_password="Start-Password-123!",
            new_password="Replacement-Password-456!",
        ),
        current_user=user,
        db=db_session,
    )

    refreshed = db_session.get(UserSecurityState, user.id)
    assert refreshed is not None
    assert refreshed.session_version == 2
    assert refreshed.must_change_password is False
    assert verify_password("Replacement-Password-456!", user.password_hash)
    assert response.user.must_change_password is False
    assert response.access_token != old_token

    with pytest.raises(HTTPException) as exc:
        get_current_user(
            credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials=old_token),
            x_user_id=None,
            db=db_session,
        )
    assert exc.value.status_code == 401


def test_admin_password_reset_forces_rotation_and_invalidates_existing_token(db_session):
    admin = make_user(db_session, "admin", UserRole.admin)
    user = make_user(db_session, "reset-target", UserRole.agent)
    state = db_session.get(UserSecurityState, user.id)
    assert state is not None
    old_token = create_access_token(user.id, session_version=state.session_version)

    reset_user_password(
        user.id,
        PasswordResetRequest(password="Admin-Reset-Password-789!"),
        db=db_session,
        current_user=admin,
    )

    refreshed = db_session.get(UserSecurityState, user.id)
    assert refreshed is not None
    assert refreshed.session_version == 2
    assert refreshed.must_change_password is True
    assert verify_password("Admin-Reset-Password-789!", user.password_hash)

    with pytest.raises(HTTPException) as exc:
        get_current_user(
            credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials=old_token),
            x_user_id=None,
            db=db_session,
        )
    assert exc.value.status_code == 401


def test_admin_logout_all_revokes_sessions_without_changing_password(db_session):
    admin = make_user(db_session, "admin-logout", UserRole.admin)
    user = make_user(db_session, "logout-target", UserRole.agent)
    original_hash = user.password_hash

    result = admin_logout_all_sessions(user.id, db=db_session, current_user=admin)

    assert result.session_version == 2
    assert user.password_hash == original_hash
