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
from app import operator_models as _operator_models  # noqa: E402,F401
from app import voice_models as _voice_models  # noqa: E402,F401
from app import webchat_models as _webchat_models  # noqa: E402,F401
from app.auth_service import create_access_token, hash_password  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AdminAuditLog, Market, Team, User  # noqa: E402

STRONG_PASSWORD = "Nexus!Admin2026"
NEXT_PASSWORD = "Nexus!Changed2026"
RESET_PASSWORD = "Nexus!Reset2026"


def _headers(user: User) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {create_access_token(user.id, user.updated_at)}",
    }


def _user(db_session, username: str, role: UserRole, *, password: str = STRONG_PASSWORD, team_id: int | None = None) -> User:
    row = User(
        username=username,
        display_name=username.replace("_", " ").title(),
        email=f"{username}@example.test",
        password_hash=hash_password(password),
        role=role,
        team_id=team_id,
        is_active=True,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _client(tmp_path):
    db_file = tmp_path / "identity-control-plane.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    db_session = TestingSession()

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    return TestClient(app), db_session, engine


def _close(db_session, engine) -> None:
    app.dependency_overrides.pop(get_db, None)
    db_session.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_self_service_password_change_revokes_http_session_and_redacts_audit(tmp_path):
    client, db_session, engine = _client(tmp_path)
    try:
        operator = _user(db_session, "operator_account", UserRole.agent)
        db_session.commit()
        headers = _headers(operator)

        assert client.get("/api/auth/me", headers=headers).status_code == 200

        wrong = client.post(
            "/api/auth/change-password",
            headers=headers,
            json={"current_password": "wrong", "new_password": NEXT_PASSWORD},
        )
        assert wrong.status_code == 400

        weak = client.post(
            "/api/auth/change-password",
            headers=headers,
            json={"current_password": STRONG_PASSWORD, "new_password": "weak-password"},
        )
        assert weak.status_code == 400

        changed = client.post(
            "/api/auth/change-password",
            headers=headers,
            json={"current_password": STRONG_PASSWORD, "new_password": NEXT_PASSWORD},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json() == {"ok": True, "reauthenticate": True}

        assert client.get("/api/auth/me", headers=headers).status_code == 401
        assert client.post("/api/auth/login", json={"username": operator.username, "password": STRONG_PASSWORD}).status_code == 401
        assert client.post("/api/auth/login", json={"username": operator.username, "password": NEXT_PASSWORD}).status_code == 200

        audit = db_session.query(AdminAuditLog).filter(AdminAuditLog.action == "auth.password_changed").one()
        assert audit.actor_id == operator.id
        assert audit.target_id == operator.id
        assert "password" not in str(audit.old_value_json or "").lower()
        assert "password" not in str(audit.new_value_json or "").lower()
        assert NEXT_PASSWORD not in str(audit.new_value_json or "")
    finally:
        _close(db_session, engine)


def test_admin_password_reset_and_user_update_revoke_existing_tokens(tmp_path):
    client, db_session, engine = _client(tmp_path)
    try:
        admin = _user(db_session, "identity_admin", UserRole.admin)
        agent = _user(db_session, "identity_agent", UserRole.agent)
        db_session.commit()
        admin_headers = _headers(admin)
        agent_headers = _headers(agent)

        reset = client.post(
            f"/api/admin/users/{agent.id}/reset-password",
            headers=admin_headers,
            json={"password": RESET_PASSWORD},
        )
        assert reset.status_code == 200, reset.text
        assert client.get("/api/auth/me", headers=agent_headers).status_code == 401

        login = client.post("/api/auth/login", json={"username": agent.username, "password": RESET_PASSWORD})
        assert login.status_code == 200, login.text
        fresh_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        updated = client.patch(
            f"/api/admin/users/{agent.id}",
            headers=admin_headers,
            json={"display_name": "Identity Agent Updated"},
        )
        assert updated.status_code == 200, updated.text
        assert client.get("/api/auth/me", headers=fresh_headers).status_code == 401
    finally:
        _close(db_session, engine)


def test_role_catalog_is_server_authoritative_and_admin_only(tmp_path):
    client, db_session, engine = _client(tmp_path)
    try:
        admin = _user(db_session, "role_admin", UserRole.admin)
        agent = _user(db_session, "role_agent", UserRole.agent)
        db_session.commit()

        response = client.get("/api/admin/identity/roles", headers=_headers(admin))
        assert response.status_code == 200, response.text
        rows = response.json()
        assert {item["role"] for item in rows} == {role.value for role in UserRole}
        admin_row = next(item for item in rows if item["role"] == "admin")
        agent_row = next(item for item in rows if item["role"] == "agent")
        assert "user.manage" in admin_row["default_capabilities"]
        assert "operator_queue.read" in agent_row["default_capabilities"]
        assert "user.manage" not in agent_row["default_capabilities"]

        assert client.get("/api/admin/identity/roles", headers=_headers(agent)).status_code == 403
    finally:
        _close(db_session, engine)


def test_team_governance_and_explicit_user_team_clear(tmp_path):
    client, db_session, engine = _client(tmp_path)
    try:
        admin = _user(db_session, "team_admin", UserRole.admin)
        market = Market(code="ME", name="Montenegro", country_code="ME", is_active=True)
        db_session.add(market)
        db_session.commit()
        headers = _headers(admin)

        created = client.post(
            "/api/admin/identity/teams",
            headers=headers,
            json={"name": "Montenegro Support", "team_type": "support", "market_id": market.id},
        )
        assert created.status_code == 200, created.text
        team = created.json()
        assert team["market_id"] == market.id
        assert team["active_users"] == 0

        duplicate = client.post(
            "/api/admin/identity/teams",
            headers=headers,
            json={"name": "montenegro support", "team_type": "support", "market_id": market.id},
        )
        assert duplicate.status_code == 400

        agent = _user(db_session, "team_agent", UserRole.agent, team_id=team["id"])
        db_session.commit()

        blocked = client.patch(
            f"/api/admin/identity/teams/{team['id']}",
            headers=headers,
            json={"is_active": False},
        )
        assert blocked.status_code == 400

        cleared = client.delete(f"/api/admin/identity/users/{agent.id}/team", headers=headers)
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["team_id"] is None

        disabled = client.patch(
            f"/api/admin/identity/teams/{team['id']}",
            headers=headers,
            json={"is_active": False, "name": "Montenegro Support Archive"},
        )
        assert disabled.status_code == 200, disabled.text
        assert disabled.json()["is_active"] is False

        rows = client.get("/api/admin/identity/teams", headers=headers)
        assert rows.status_code == 200
        assert any(item["id"] == team["id"] and item["is_active"] is False for item in rows.json())
    finally:
        _close(db_session, engine)
