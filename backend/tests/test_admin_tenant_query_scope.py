from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")
os.environ.setdefault("SECRET_KEY", "admin-tenant-query-scope-secret-long-enough")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models as _models  # noqa: E402,F401
from app import models_identity_policy as _identity_models  # noqa: E402,F401
from app.auth_service import create_access_token, hash_password  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AdminAuditLog, Market, OutboundEmailAccount, Tenant, User  # noqa: E402
from app.models_identity_policy import UserCredentialPolicy  # noqa: E402
from app.services.secret_crypto import SecretCryptoService  # noqa: E402

PASSWORD = "Nexus!TenantScope2026"


def _client(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'admin-tenant-scope.db'}",
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


def _account(db, *, name: str, market_id: int, actor_id: int) -> OutboundEmailAccount:
    encrypted = SecretCryptoService.outbound_email().encrypt(PASSWORD)
    assert encrypted
    row = OutboundEmailAccount(
        display_name=name,
        host=f"smtp.{name.lower().replace(' ', '-')}.test",
        port=587,
        username=f"{name.lower().replace(' ', '-')}@example.test",
        password_encrypted=encrypted,
        from_address=f"{name.lower().replace(' ', '-')}@example.test",
        security_mode="starttls",
        inbound_enabled=False,
        market_id=market_id,
        is_active=True,
        priority=100,
        health_status="unknown",
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(row)
    db.flush()
    return row


def test_admin_security_and_email_queries_are_tenant_scoped(tmp_path):
    client, db, engine = _client(tmp_path)
    try:
        tenant_a = Tenant(tenant_key="admin-scope-a", display_name="Admin Scope A", is_active=True)
        tenant_b = Tenant(tenant_key="admin-scope-b", display_name="Admin Scope B", is_active=True)
        db.add_all([tenant_a, tenant_b])
        db.flush()

        market_a = Market(tenant_id=tenant_a.id, code="SA", name="Scope Market A", country_code="AA", is_active=True)
        market_b = Market(tenant_id=tenant_b.id, code="SB", name="Scope Market B", country_code="BB", is_active=True)
        db.add_all([market_a, market_b])
        db.flush()

        admin_a = _user(db, "scope_admin_a", tenant_a.id, UserRole.admin)
        agent_a = _user(db, "scope_agent_a", tenant_a.id, UserRole.agent)
        admin_b = _user(db, "scope_admin_b", tenant_b.id, UserRole.admin)
        agent_b = _user(db, "scope_agent_b", tenant_b.id, UserRole.agent)
        db.flush()

        account_a = _account(db, name="Mail A", market_id=market_a.id, actor_id=admin_a.id)
        account_b = _account(db, name="Mail B", market_id=market_b.id, actor_id=admin_b.id)
        db.add_all([
            AdminAuditLog(
                actor_id=admin_a.id,
                action="user.update",
                target_type="user",
                target_id=agent_a.id,
                old_value_json="{}",
                new_value_json='{"display_name":"A"}',
            ),
            AdminAuditLog(
                actor_id=admin_b.id,
                action="user.update",
                target_type="user",
                target_id=agent_b.id,
                old_value_json="{}",
                new_value_json='{"display_name":"B"}',
            ),
        ])
        db.commit()

        headers = {"Authorization": f"Bearer {create_access_token(admin_a.id, admin_a.updated_at)}"}

        audit = client.get("/api/admin/security-audit?limit=100", headers=headers)
        assert audit.status_code == 200, audit.text
        assert {row["username"] for row in audit.json()["users"]} == {"scope_admin_a", "scope_agent_a"}
        assert {row["actor_username"] for row in audit.json()["recent_audit"]} == {"scope_admin_a"}
        assert audit.json()["summary"]["total_users"] == 2

        listed = client.get("/api/admin/outbound-email/accounts", headers=headers)
        assert listed.status_code == 200, listed.text
        assert [row["id"] for row in listed.json()] == [account_a.id]
        assert account_b.id not in {row["id"] for row in listed.json()}

        assert client.get(
            f"/api/admin/outbound-email/accounts/{account_b.id}",
            headers=headers,
        ).status_code == 404
        assert client.post(
            f"/api/admin/outbound-email/accounts/{account_b.id}/disable",
            headers=headers,
        ).status_code == 404
        assert client.patch(
            f"/api/admin/outbound-email/accounts/{account_b.id}",
            headers=headers,
            json={"display_name": "Cross Tenant"},
        ).status_code == 404

        unowned = client.post(
            "/api/admin/outbound-email/accounts",
            headers=headers,
            json={
                "display_name": "Unowned Mail",
                "host": "smtp.unowned.test",
                "port": 587,
                "username": "unowned@example.test",
                "password": PASSWORD,
                "from_address": "unowned@example.test",
                "security_mode": "starttls",
                "inbound_enabled": False,
                "is_active": True,
                "priority": 100,
            },
        )
        assert unowned.status_code == 400
        assert "market_id is required" in str(unowned.json()["detail"])

        cross_market = client.post(
            "/api/admin/outbound-email/accounts",
            headers=headers,
            json={
                "display_name": "Cross Market Mail",
                "host": "smtp.cross-market.test",
                "port": 587,
                "username": "cross-market@example.test",
                "password": PASSWORD,
                "from_address": "cross-market@example.test",
                "security_mode": "starttls",
                "inbound_enabled": False,
                "market_id": market_b.id,
                "is_active": True,
                "priority": 100,
            },
        )
        assert cross_market.status_code == 404

        created = client.post(
            "/api/admin/outbound-email/accounts",
            headers=headers,
            json={
                "display_name": "Owned Mail",
                "host": "smtp.owned.test",
                "port": 587,
                "username": "owned@example.test",
                "password": PASSWORD,
                "from_address": "owned@example.test",
                "security_mode": "starttls",
                "inbound_enabled": False,
                "market_id": market_a.id,
                "is_active": True,
                "priority": 90,
            },
        )
        assert created.status_code == 200, created.text
        assert created.json()["market_id"] == market_a.id
    finally:
        _close(db, engine)
