import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/admin_security_audit_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.auth_service import create_access_token  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AdminAuditLog, User, UserCapabilityOverride  # noqa: E402
from app.services.permissions import CAP_AUDIT_READ, CAP_SECURITY_READ  # noqa: E402


@pytest.fixture()
def db_session():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def client():
    return TestClient(app, raise_server_exceptions=False)


def _user(db, user_id: int, username: str, role: UserRole) -> User:
    row = User(
        id=user_id,
        username=username,
        display_name=username.replace("-", " ").title(),
        email=f"{username}@example.test",
        password_hash="x",
        role=role,
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def _seed(db):
    admin = _user(db, 9701, "security-admin", UserRole.admin)
    auditor = _user(db, 9702, "security-auditor", UserRole.auditor)
    agent = _user(db, 9703, "security-agent", UserRole.agent)
    db.add(UserCapabilityOverride(user_id=agent.id, capability="runtime.manage", allowed=True))
    db.add(UserCapabilityOverride(user_id=auditor.id, capability="customer_profile.read", allowed=False))
    db.add(
        AdminAuditLog(
            actor_id=admin.id,
            action="user.capability.update",
            target_type="user",
            target_id=auditor.id,
            old_value_json=json.dumps({"capabilities": ["ticket.read"], "password": "old-secret"}),
            new_value_json=json.dumps(
                {
                    "capabilities": ["ticket.read", CAP_SECURITY_READ, CAP_AUDIT_READ],
                    "nested": {"access_token": "token-value", "request_id": "req-visible"},
                }
            ),
        )
    )
    db.commit()
    return admin, auditor, agent


def test_admin_security_audit_is_readable_by_auditor_and_redacts_secret_values(client, db_session):
    admin, auditor, _agent = _seed(db_session)

    response = client.get("/api/admin/security-audit", headers=_headers(auditor))

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    assert payload["contracts"]["readonly"] is True
    assert payload["contracts"]["auditor_readonly"] is True
    assert payload["contracts"]["mutation_api_exposed"] is False
    assert payload["contracts"]["can_manage_users"] is False
    assert set(payload["contracts"]["required_capabilities"]) == {CAP_SECURITY_READ, CAP_AUDIT_READ}

    capability_names = {row["capability"] for row in payload["capability_matrix"]}
    assert {CAP_SECURITY_READ, CAP_AUDIT_READ}.issubset(capability_names)
    auditor_role = next(row for row in payload["role_matrix"] if row["role"] == "auditor")
    assert CAP_SECURITY_READ in auditor_role["capabilities"]
    assert CAP_AUDIT_READ in auditor_role["capabilities"]

    user_rows = {row["username"]: row for row in payload["users"]}
    assert user_rows[admin.username]["risk"] == "high"
    assert user_rows[auditor.username]["deny_override_count"] == 1

    audit = payload["audit_logs"][0]
    assert audit["action"] == "user.capability.update"
    assert audit["actor_display_name"] == admin.display_name
    assert "capabilities" in audit["changed_fields"]
    serialized = json.dumps(audit, ensure_ascii=False)
    assert "old-secret" not in serialized
    assert "token-value" not in serialized
    assert "[redacted]" in serialized
    assert "req-visible" in serialized


def test_admin_security_audit_admin_can_read_manage_signal(client, db_session):
    admin, _auditor, _agent = _seed(db_session)

    response = client.get("/api/admin/security-audit?limit=1&q=capability", headers=_headers(admin))

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["contracts"]["can_manage_users"] is True
    assert payload["summary"]["recent_audit_count"] == 1
    assert len(payload["audit_logs"]) == 1


def test_admin_security_audit_blocks_agent_without_read_capability(client, db_session):
    _admin, _auditor, agent = _seed(db_session)

    response = client.get("/api/admin/security-audit", headers=_headers(agent))

    assert response.status_code == 403
    assert response.json()["detail"] == "Not authorized to read security audit"
