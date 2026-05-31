from __future__ import annotations

import json
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
from app import operator_models as _operator_models  # noqa: E402,F401
from app import voice_models as _voice_models  # noqa: E402,F401
from app import webchat_models as _webchat_models  # noqa: E402,F401
from app.auth_service import create_access_token  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AdminAuditLog, User, UserCapabilityOverride  # noqa: E402
from app.utils.time import utc_now  # noqa: E402


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


def _user(db_session, username: str, role: UserRole) -> User:
    row = User(
        username=username,
        display_name=username.replace("_", " ").title(),
        email=f"{username}@example.test",
        password_hash="x",
        role=role,
        is_active=True,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_security_audit_lens_contract_and_redaction(tmp_path):
    db_file = tmp_path / "security-audit.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)
    db_session = TestingSession()

    admin = _user(db_session, "admin_sec", UserRole.admin)
    auditor = _user(db_session, "auditor_sec", UserRole.auditor)
    agent = _user(db_session, "agent_sec", UserRole.agent)
    db_session.add(UserCapabilityOverride(user_id=auditor.id, capability="runtime.manage", allowed=True))
    db_session.add(
        AdminAuditLog(
            actor_id=admin.id,
            action="user.reset_password",
            target_type="user",
            target_id=agent.id,
            old_value_json=json.dumps({"password": "old-secret", "display_name": "Agent Sec"}),
            new_value_json=json.dumps({"api_token": "abc123", "profile": {"credential": "smtp-pass", "role": "agent"}}),
            created_at=utc_now(),
        )
    )
    db_session.commit()

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        admin_response = client.get("/api/admin/security-audit?limit=10", headers=_headers(admin))
        auditor_response = client.get("/api/admin/security-audit", headers=_headers(auditor))
        agent_response = client.get("/api/admin/security-audit", headers=_headers(agent))
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert admin_response.status_code == 200, admin_response.text
    payload = admin_response.json()
    assert "security.read" in payload["capability_catalog"]
    assert "audit.read" in payload["capability_catalog"]
    assert payload["summary"]["read_only"] is False
    assert payload["summary"]["total_users"] == 3
    assert payload["summary"]["auditor_users"] == 1
    assert payload["summary"]["high_risk_overrides"] == 1
    assert payload["summary"]["recent_audit_24h"] == 1

    audit_row = payload["recent_audit"][0]
    assert audit_row["actor_username"] == "admin_sec"
    assert audit_row["old_value"]["password"] == "[redacted]"
    assert audit_row["old_value"]["display_name"] == "Agent Sec"
    assert audit_row["new_value"]["api_token"] == "[redacted]"
    assert audit_row["new_value"]["profile"]["credential"] == "[redacted]"
    assert audit_row["new_value"]["profile"]["role"] == "agent"

    auditor_payload = auditor_response.json()
    assert auditor_response.status_code == 200, auditor_response.text
    assert auditor_payload["summary"]["read_only"] is True
    auditor_row = next(row for row in auditor_payload["users"] if row["username"] == "auditor_sec")
    assert "security.read" in auditor_row["effective_capabilities"]
    assert "audit.read" in auditor_row["effective_capabilities"]
    assert auditor_row["override_count"] == 1

    assert agent_response.status_code == 403

    db_session.close()
    Base.metadata.drop_all(engine)
    engine.dispose()
