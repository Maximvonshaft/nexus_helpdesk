from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models as _models  # noqa: E402,F401
from app import models_identity_policy as _identity_models  # noqa: E402,F401
from app.auth_service import create_access_token, hash_password  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: E402
from app.models_identity_policy import UserCredentialPolicy  # noqa: E402

INITIAL_PASSWORD = "Nexus!Issued2026"
ROTATED_PASSWORD = "Nexus!Rotated2026"


def _user(db, username: str, role: UserRole) -> User:
    row = User(
        username=username,
        display_name=username.replace("_", " ").title(),
        email=f"{username}@example.test",
        password_hash=hash_password(INITIAL_PASSWORD),
        role=role,
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id, user.updated_at)}"}


def test_generic_insert_is_not_an_interactive_account_creation(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'generic-user.db'}",
        connect_args={"check_same_thread": False},
    )
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    db = Session()
    try:
        user = _user(db, "bootstrap_operator", UserRole.agent)
        db.commit()

        policy = db.get(UserCredentialPolicy, user.id)
        assert policy is not None
        assert policy.must_change_password is False
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_admin_user_creation_forces_rotation_until_password_is_changed(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'admin-issued-user.db'}",
        connect_args={"check_same_thread": False},
    )
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    db = Session()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        admin = _user(db, "creation_admin", UserRole.admin)
        db.commit()

        created = client.post(
            "/api/admin/users",
            headers=_headers(admin),
            json={
                "username": "issued_agent",
                "display_name": "Issued Agent",
                "email": "issued_agent@example.test",
                "password": INITIAL_PASSWORD,
                "role": "agent",
                "team_id": None,
                "capabilities": ["ticket.read", "operator_queue.read"],
            },
        )
        assert created.status_code == 200, created.text
        user_id = created.json()["id"]

        policy = db.get(UserCredentialPolicy, user_id)
        assert policy is not None
        assert policy.must_change_password is True

        login = client.post(
            "/api/auth/login",
            json={"username": "issued_agent", "password": INITIAL_PASSWORD},
        )
        assert login.status_code == 200, login.text
        assert login.json()["user"]["must_change_password"] is True
        recovery_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        assert client.get("/api/auth/me", headers=recovery_headers).status_code == 200
        blocked = client.get("/api/lookups/teams", headers=recovery_headers)
        assert blocked.status_code == 403
        assert blocked.json()["detail"] == "Password change required"

        changed = client.post(
            "/api/auth/change-password",
            headers=recovery_headers,
            json={
                "current_password": INITIAL_PASSWORD,
                "new_password": ROTATED_PASSWORD,
            },
        )
        assert changed.status_code == 200, changed.text
        assert client.get("/api/auth/me", headers=recovery_headers).status_code == 401

        next_login = client.post(
            "/api/auth/login",
            json={"username": "issued_agent", "password": ROTATED_PASSWORD},
        )
        assert next_login.status_code == 200, next_login.text
        assert next_login.json()["user"]["must_change_password"] is False
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
