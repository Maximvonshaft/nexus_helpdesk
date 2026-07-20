from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")
os.environ.setdefault("SECRET_KEY", "admin-identity-request-policy-secret-long-enough")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models as _models  # noqa: E402,F401
from app import models_identity_policy as _identity_models  # noqa: E402,F401
from app.auth_service import create_access_token, hash_password  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Tenant, User  # noqa: E402
from app.models_identity_policy import UserCredentialPolicy  # noqa: E402

PASSWORD = "Nexus!Policy2026"


def _client(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'admin-identity-policy.db'}",
        connect_args={"check_same_thread": False},
    )
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    db = Session()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    return TestClient(app), db, engine


def _close(db, engine) -> None:
    app.dependency_overrides.pop(get_db, None)
    db.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


def _user(db, username: str, tenant_id: int, role: UserRole) -> User:
    row = User(
        username=username,
        display_name=username.replace('_', ' ').title(),
        email=f"{username}@example.test",
        password_hash=hash_password(PASSWORD),
        tenant_id=tenant_id,
        role=role,
        is_active=True,
    )
    db.add(row)
    db.flush()
    policy = db.get(UserCredentialPolicy, row.id)
    assert policy is not None
    policy.must_change_password = False
    db.flush()
    return row


def test_user_create_inherits_actor_tenant_and_final_admin_cannot_drop_manage(tmp_path):
    client, db, engine = _client(tmp_path)
    try:
        tenant = Tenant(tenant_key="policy-tenant", display_name="Policy Tenant", is_active=True)
        db.add(tenant)
        db.flush()
        admin = _user(db, "policy_admin", tenant.id, UserRole.admin)
        db.commit()
        headers = {"Authorization": f"Bearer {create_access_token(admin.id, admin.updated_at)}"}

        created = client.post(
            "/api/admin/users",
            headers=headers,
            json={
                "username": "policy_agent",
                "display_name": "Policy Agent",
                "email": "policy_agent@example.test",
                "password": PASSWORD,
                "role": "agent",
                "team_id": None,
                "capabilities": ["ticket.read", "operator_queue.read"],
            },
        )
        assert created.status_code == 200, created.text
        created_user = db.get(User, created.json()["id"])
        assert created_user is not None
        assert created_user.tenant_id == tenant.id
        assert created_user.tenant_assignment_source == "runtime_principal"
        assert created_user.tenant_assignment_version == "nexus.tenant.runtime_authority.v1"

        lockout = client.patch(
            f"/api/admin/users/{admin.id}",
            headers=headers,
            json={"capabilities": ["security.read", "audit.read"]},
        )
        assert lockout.status_code == 400
        assert lockout.json()["detail"] == "The final active administrator must retain user.manage"

        db.refresh(admin)
        assert admin.role == UserRole.admin
    finally:
        _close(db, engine)
