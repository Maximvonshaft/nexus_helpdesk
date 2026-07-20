from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")
os.environ.setdefault("SECRET_KEY", "admin-tenant-control-plane-secret-long-enough")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models as _models  # noqa: E402,F401
from app import models_identity_policy as _identity_models  # noqa: E402,F401
from app.auth_service import create_access_token, hash_password  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    AdminAuditLog,
    Market,
    OutboundEmailAccount,
    Tenant,
    User,
    UserCapabilityOverride,
)
from app.models_identity_policy import UserCredentialPolicy  # noqa: E402

PASSWORD = "Nexus!TenantAdmin2026"


def _client(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'admin-tenant-control-plane.db'}",
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
        display_name=username.replace("_", " ").title(),
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


def _email_account(db, *, market_id: int, suffix: str, creator_id: int) -> OutboundEmailAccount:
    row = OutboundEmailAccount(
        display_name=f"Email {suffix}",
        host=f"smtp-{suffix}.example.test",
        port=587,
        username=f"mailer-{suffix}",
        password_encrypted="encrypted-placeholder",
        from_address=f"support-{suffix}@example.test",
        security_mode="starttls",
        inbound_enabled=False,
        market_id=market_id,
        is_active=True,
        priority=100,
        health_status="unknown",
        created_by=creator_id,
        updated_by=creator_id,
    )
    db.add(row)
    db.flush()
    return row


def test_admin_control_plane_is_tenant_scoped_for_users_audit_and_email(tmp_path):
    client, db, engine = _client(tmp_path)
    try:
        tenant_a = Tenant(tenant_key="admin-a", display_name="Admin Tenant A", is_active=True)
        tenant_b = Tenant(tenant_key="admin-b", display_name="Admin Tenant B", is_active=True)
        db.add_all([tenant_a, tenant_b])
        db.flush()

        market_a = Market(
            tenant_id=tenant_a.id,
            code="AA",
            name="Admin Market A",
            country_code="AA",
            is_active=True,
        )
        market_b = Market(
            tenant_id=tenant_b.id,
            code="BB",
            name="Admin Market B",
            country_code="BB",
            is_active=True,
        )
        db.add_all([market_a, market_b])
        db.flush()

        admin_a = _user(db, "admin_a_scope", tenant_a.id, UserRole.admin)
        agent_a = _user(db, "agent_a_scope", tenant_a.id, UserRole.agent)
        admin_b = _user(db, "admin_b_scope", tenant_b.id, UserRole.admin)
        agent_b = _user(db, "agent_b_scope", tenant_b.id, UserRole.agent)
        db.flush()

        db.add_all([
            UserCapabilityOverride(user_id=agent_a.id, capability="audit.read", allowed=True),
            UserCapabilityOverride(user_id=agent_b.id, capability="audit.read", allowed=True),
            AdminAuditLog(
                actor_id=admin_a.id,
                action="tenant_a.action",
                target_type="user",
                target_id=agent_a.id,
            ),
            AdminAuditLog(
                actor_id=admin_b.id,
                action="tenant_b.action",
                target_type="user",
                target_id=agent_b.id,
            ),
        ])
        email_a = _email_account(db, market_id=market_a.id, suffix="a", creator_id=admin_a.id)
        email_b = _email_account(db, market_id=market_b.id, suffix="b", creator_id=admin_b.id)
        db.commit()

        headers = {"Authorization": f"Bearer {create_access_token(admin_a.id, admin_a.updated_at)}"}

        users = client.get(
            "/api/admin/users?limit=100&include_inactive=true",
            headers=headers,
        )
        assert users.status_code == 200, users.text
        assert {row["username"] for row in users.json()["items"]} == {
            admin_a.username,
            agent_a.username,
        }

        audit = client.get("/api/admin/security-audit?limit=100", headers=headers)
        assert audit.status_code == 200, audit.text
        assert {row["username"] for row in audit.json()["users"]} == {
            admin_a.username,
            agent_a.username,
        }
        assert {row["action"] for row in audit.json()["recent_audit"]} == {"tenant_a.action"}
        assert audit.json()["summary"]["total_users"] == 2
        assert audit.json()["summary"]["active_users"] == 2

        accounts = client.get("/api/admin/outbound-email/accounts", headers=headers)
        assert accounts.status_code == 200, accounts.text
        assert [row["id"] for row in accounts.json()] == [email_a.id]
        assert email_b.id not in {row["id"] for row in accounts.json()}

        assert client.get(
            f"/api/admin/outbound-email/accounts/{email_b.id}",
            headers=headers,
        ).status_code == 404

        missing_market = client.post(
            "/api/admin/outbound-email/accounts",
            headers=headers,
            json={
                "host": "smtp-missing.example.test",
                "port": 587,
                "username": "missing-market",
                "password": PASSWORD,
                "from_address": "missing-market@example.test",
                "security_mode": "starttls",
                "priority": 100,
                "is_active": True,
            },
        )
        assert missing_market.status_code == 400
        assert missing_market.json()["detail"] == "market_id is required for a tenant-bound email account"

        cross_market = client.post(
            "/api/admin/outbound-email/accounts",
            headers=headers,
            json={
                "host": "smtp-cross.example.test",
                "port": 587,
                "username": "cross-market",
                "password": PASSWORD,
                "from_address": "cross-market@example.test",
                "security_mode": "starttls",
                "market_id": market_b.id,
                "priority": 100,
                "is_active": True,
            },
        )
        assert cross_market.status_code == 400
        assert cross_market.json()["detail"] == "Market not found or inactive"

        created = client.post(
            "/api/admin/outbound-email/accounts",
            headers=headers,
            json={
                "display_name": "Tenant A SMTP",
                "host": "smtp-new-a.example.test",
                "port": 587,
                "username": "new-a",
                "password": PASSWORD,
                "from_address": "new-a@example.test",
                "security_mode": "starttls",
                "market_id": market_a.id,
                "priority": 100,
                "is_active": True,
            },
        )
        assert created.status_code == 200, created.text
        assert created.json()["market_id"] == market_a.id
    finally:
        _close(db, engine)
