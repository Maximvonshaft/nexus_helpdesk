from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")
os.environ.setdefault("SECRET_KEY", "identity-governance-tenant-test-secret-long-enough")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models as _models  # noqa: E402,F401
from app import models_identity_policy as _identity_models  # noqa: E402,F401
from app.auth_service import create_access_token, hash_password  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Market, Team, Tenant, User  # noqa: E402
from app.models_identity_policy import UserCredentialPolicy  # noqa: E402


def _client(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'identity-tenant.db'}",
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


def _user(db, username: str, role: UserRole, tenant_id: int, *, team_id: int | None = None) -> User:
    row = User(
        username=username,
        display_name=username.replace('_', ' ').title(),
        email=f"{username}@example.test",
        password_hash=hash_password("Nexus!Tenant2026"),
        role=role,
        tenant_id=tenant_id,
        team_id=team_id,
        is_active=True,
    )
    db.add(row)
    db.flush()
    policy = db.get(UserCredentialPolicy, row.id)
    assert policy is not None
    policy.must_change_password = False
    db.flush()
    return row


def test_identity_governance_is_tenant_scoped_end_to_end(tmp_path):
    client, db, engine = _client(tmp_path)
    try:
        tenant_a = Tenant(tenant_key="tenant-a", display_name="Tenant A", is_active=True)
        tenant_b = Tenant(tenant_key="tenant-b", display_name="Tenant B", is_active=True)
        db.add_all([tenant_a, tenant_b])
        db.flush()

        market_a = Market(
            tenant_id=tenant_a.id,
            code="AA",
            name="Market A",
            country_code="AA",
            is_active=True,
        )
        market_b = Market(
            tenant_id=tenant_b.id,
            code="BB",
            name="Market B",
            country_code="BB",
            is_active=True,
        )
        db.add_all([market_a, market_b])
        db.flush()

        team_a = Team(
            tenant_id=tenant_a.id,
            name="Tenant A Support",
            team_type="support",
            market_id=market_a.id,
            is_active=True,
        )
        team_b = Team(
            tenant_id=tenant_b.id,
            name="Tenant B Support",
            team_type="support",
            market_id=market_b.id,
            is_active=True,
        )
        db.add_all([team_a, team_b])
        db.flush()

        admin_a = _user(db, "admin_a", UserRole.admin, tenant_a.id)
        agent_a = _user(db, "agent_a", UserRole.agent, tenant_a.id, team_id=team_a.id)
        agent_b = _user(db, "agent_b", UserRole.agent, tenant_b.id, team_id=team_b.id)
        db.commit()

        headers = {"Authorization": f"Bearer {create_access_token(admin_a.id, admin_a.updated_at)}"}

        policies = client.get("/api/admin/identity/credential-policies", headers=headers)
        assert policies.status_code == 200, policies.text
        usernames = {row["username"] for row in policies.json()}
        assert usernames == {"admin_a", "agent_a"}
        assert "agent_b" not in usernames

        teams = client.get("/api/admin/identity/teams", headers=headers)
        assert teams.status_code == 200, teams.text
        assert {row["id"] for row in teams.json()} == {team_a.id}

        assert client.post(
            f"/api/admin/identity/users/{agent_b.id}/revoke-sessions",
            headers=headers,
        ).status_code == 404
        assert client.post(
            f"/api/admin/identity/users/{agent_b.id}/require-password-change",
            headers=headers,
        ).status_code == 404
        assert client.delete(
            f"/api/admin/identity/users/{agent_b.id}/team",
            headers=headers,
        ).status_code == 404
        assert client.patch(
            f"/api/admin/identity/teams/{team_b.id}",
            headers=headers,
            json={"name": "Cross Tenant Rename"},
        ).status_code == 404

        cross_market = client.post(
            "/api/admin/identity/teams",
            headers=headers,
            json={"name": "Invalid Cross Market Team", "team_type": "support", "market_id": market_b.id},
        )
        assert cross_market.status_code == 400

        created = client.post(
            "/api/admin/identity/teams",
            headers=headers,
            json={"name": "Tenant A Escalations", "team_type": "support", "market_id": market_a.id},
        )
        assert created.status_code == 200, created.text
        created_row = db.get(Team, created.json()["id"])
        assert created_row is not None
        assert created_row.tenant_id == tenant_a.id
        assert created_row.tenant_assignment_source == "runtime_principal"
        assert created_row.tenant_assignment_version == "nexus.tenant.runtime_authority.v1"
    finally:
        _close(db, engine)
