from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.auth_service import create_access_token  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AdminAuditLog, User, UserCapabilityOverride  # noqa: E402
from app.services.permissions import CAP_AUDIT_READ, CAP_RUNTIME_MANAGE  # noqa: E402


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _user(db, role: UserRole, prefix: str) -> User:
    username = _unique(prefix)
    row = User(
        username=username,
        display_name=f"{role.value.title()} User",
        email=f"{username}@example.test",
        password_hash="test",
        role=role,
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def test_auditor_can_read_permissions_audit_dashboard(client: TestClient, db_session):
    admin = _user(db_session, UserRole.admin, "permissions-admin")
    auditor = _user(db_session, UserRole.auditor, "permissions-auditor")
    _user(db_session, UserRole.manager, "permissions-manager")
    agent = _user(db_session, UserRole.agent, "permissions-agent")
    db_session.add(UserCapabilityOverride(user_id=agent.id, capability=CAP_RUNTIME_MANAGE, allowed=True))
    db_session.add(
        AdminAuditLog(
            actor_id=admin.id,
            action="user.capability.update",
            target_type="user",
            target_id=agent.id,
            old_value_json=json.dumps({"capabilities": []}),
            new_value_json=json.dumps({"capabilities": [CAP_RUNTIME_MANAGE], "role": "agent"}),
        )
    )
    db_session.commit()

    response = client.get("/api/admin/permissions-audit?limit=10", headers=_headers(auditor))

    assert response.status_code == 200, response.text
    payload = response.json()
    assert CAP_AUDIT_READ in payload["capability_catalog"]
    assert payload["summary"]["total_users"] == 4
    assert payload["summary"]["active_users"] == 4
    assert payload["summary"]["admin_users"] == 1
    assert payload["summary"]["auditor_users"] == 1
    assert payload["summary"]["high_risk_override_count"] == 1
    assert payload["summary"]["recent_audit_count"] == 1

    serialized_agent = next(item for item in payload["users"] if item["id"] == agent.id)
    assert CAP_RUNTIME_MANAGE not in serialized_agent["base_capabilities"]
    assert CAP_RUNTIME_MANAGE in serialized_agent["effective_capabilities"]
    assert serialized_agent["overrides"] == [
        {
            "id": serialized_agent["overrides"][0]["id"],
            "user_id": agent.id,
            "capability": CAP_RUNTIME_MANAGE,
            "allowed": True,
            "created_at": serialized_agent["overrides"][0]["created_at"],
            "updated_at": serialized_agent["overrides"][0]["updated_at"],
        }
    ]

    audit = payload["audit_logs"][0]
    assert audit["actor_id"] == admin.id
    assert audit["actor_username"] == admin.username
    assert audit["actor_display_name"] == admin.display_name
    assert audit["action"] == "user.capability.update"
    assert audit["target_type"] == "user"
    assert audit["target_id"] == agent.id
    assert audit["old_value"] == {"capabilities": []}
    assert audit["new_value"]["capabilities"] == [CAP_RUNTIME_MANAGE]


def test_manager_without_audit_or_user_manage_is_forbidden(client: TestClient, db_session):
    manager = _user(db_session, UserRole.manager, "permissions-manager-forbidden")
    db_session.commit()

    response = client.get("/api/admin/permissions-audit", headers=_headers(manager))

    assert response.status_code == 403
    assert response.json()["detail"] == "Not authorized to read audit"


def test_admin_can_read_permissions_audit_dashboard(client: TestClient, db_session):
    admin = _user(db_session, UserRole.admin, "permissions-admin-read")
    db_session.commit()

    response = client.get("/api/admin/permissions-audit", headers=_headers(admin))

    assert response.status_code == 200, response.text
    assert response.json()["summary"]["admin_users"] == 1
