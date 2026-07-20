from __future__ import annotations

import json
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

from app.api.admin import activate_user, deactivate_user, reset_user_password  # noqa: E402
from app.api.auth import ChangePasswordRequest, change_password  # noqa: E402
from app.api.deps import get_current_user  # noqa: E402
from app.api.identity_admin import (  # noqa: E402
    UserTeamAssignmentRequest,
    admin_logout_all_sessions,
    assign_user_team,
    clear_user_email,
    require_password_change,
)
from app.auth_service import create_access_token, decode_access_token_claims, hash_password, verify_password  # noqa: E402
from app.db import Base  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.model_registry import register_all_models  # noqa: E402
from app.models import AdminAuditLog, Team, User  # noqa: E402
from app.models_identity import UserSecurityState  # noqa: E402
from app.schemas import PasswordResetRequest  # noqa: E402

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


def assert_token_rejected(db, token: str) -> None:
    with pytest.raises(HTTPException) as exc:
        get_current_user(
            credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials=token),
            x_user_id=None,
            db=db,
        )
    assert exc.value.status_code == 401


def test_new_user_receives_one_canonical_forced_rotation_state(db_session):
    user = make_user(db_session, "new-agent", UserRole.agent)
    state = db_session.get(UserSecurityState, user.id)

    assert state is not None
    assert state.session_version == 1
    assert state.must_change_password is True


def test_self_password_change_rotates_session_clears_forced_state_and_audits_real_version(db_session):
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
    assert refreshed is state
    assert state.session_version == 2
    assert state.must_change_password is False
    assert verify_password("Replacement-Password-456!", user.password_hash)

    claims = decode_access_token_claims(response.access_token)
    assert claims is not None
    assert claims.session_version == 2
    assert_token_rejected(db_session, old_token)

    audit = (
        db_session.query(AdminAuditLog)
        .filter(AdminAuditLog.action == "user.change_password", AdminAuditLog.target_id == user.id)
        .one()
    )
    assert json.loads(audit.new_value_json or "{}") == {"session_version": 2}


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
    assert refreshed is state
    assert state.session_version == 2
    assert state.must_change_password is True
    assert verify_password("Admin-Reset-Password-789!", user.password_hash)
    assert_token_rejected(db_session, old_token)


def test_admin_logout_all_revokes_sessions_without_changing_password(db_session):
    admin = make_user(db_session, "admin-logout", UserRole.admin)
    user = make_user(db_session, "logout-target", UserRole.agent)
    original_hash = user.password_hash
    state = db_session.get(UserSecurityState, user.id)
    assert state is not None
    old_token = create_access_token(user.id, session_version=state.session_version)

    result = admin_logout_all_sessions(user.id, db=db_session, current_user=admin)

    assert result.session_version == 2
    assert state.session_version == 2
    assert user.password_hash == original_hash
    assert_token_rejected(db_session, old_token)


def test_explicit_security_commands_advance_session_version_monotonically(db_session):
    admin = make_user(db_session, "admin-monotonic", UserRole.admin)
    user = make_user(db_session, "monotonic-target", UserRole.agent)

    first = admin_logout_all_sessions(user.id, db=db_session, current_user=admin)
    second = require_password_change(user.id, db=db_session, current_user=admin)

    assert first.session_version == 2
    assert second.session_version == 3
    assert second.must_change_password is True


def test_deactivate_then_reactivate_does_not_resurrect_old_session(db_session):
    admin = make_user(db_session, "admin-status", UserRole.admin)
    user = make_user(db_session, "status-target", UserRole.agent)
    state = db_session.get(UserSecurityState, user.id)
    assert state is not None
    old_token = create_access_token(user.id, session_version=state.session_version)

    deactivate_user(user.id, db=db_session, current_user=admin)
    assert state.session_version == 2
    activate_user(user.id, db=db_session, current_user=admin)

    refreshed = db_session.get(UserSecurityState, user.id)
    assert refreshed is state
    assert state.session_version == 2
    assert user.is_active is True
    assert_token_rejected(db_session, old_token)


def test_team_assignment_and_removal_use_one_explicit_command(db_session):
    admin = make_user(db_session, "admin-team", UserRole.admin)
    user = make_user(db_session, "team-target", UserRole.agent)
    team = Team(name="Customer Care", team_type="support", is_active=True)
    db_session.add(team)
    db_session.flush()

    assigned = assign_user_team(
        user.id,
        UserTeamAssignmentRequest(team_id=team.id),
        db=db_session,
        current_user=admin,
    )
    assert assigned == {"ok": True, "user_id": user.id, "team_id": team.id}
    assert user.team_id == team.id

    removed = assign_user_team(
        user.id,
        UserTeamAssignmentRequest(team_id=None),
        db=db_session,
        current_user=admin,
    )
    assert removed == {"ok": True, "user_id": user.id, "team_id": None}
    assert user.team_id is None

    actions = [
        row.action
        for row in db_session.query(AdminAuditLog)
        .filter(AdminAuditLog.target_id == user.id)
        .order_by(AdminAuditLog.id.asc())
        .all()
    ]
    assert actions[-2:] == ["user.team.assign", "user.team.assign"]


def test_optional_email_removal_is_explicit_and_audited(db_session):
    admin = make_user(db_session, "admin-email", UserRole.admin)
    user = make_user(db_session, "email-target", UserRole.agent)
    previous_email = user.email

    result = clear_user_email(user.id, db=db_session, current_user=admin)

    assert result == {"ok": True, "user_id": user.id, "email": None}
    assert user.email is None
    audit = (
        db_session.query(AdminAuditLog)
        .filter(AdminAuditLog.action == "user.email.clear", AdminAuditLog.target_id == user.id)
        .one()
    )
    assert json.loads(audit.old_value_json or "{}") == {"email": previous_email}
    assert json.loads(audit.new_value_json or "{}") == {"email": None}
