from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")
os.environ.setdefault("SECRET_KEY", "mfa-control-plane-test-secret-long-enough")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models as _models  # noqa: E402,F401
from app import models_identity_policy as _identity_models  # noqa: E402,F401
from app.auth_service import create_access_token, hash_password  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AdminAuditLog, AuthThrottleEntry, Tenant, User  # noqa: E402
from app.models_identity_policy import UserCredentialPolicy  # noqa: E402
from app.services.mfa_service import (  # noqa: E402
    begin_mfa_setup,
    confirm_mfa_setup,
    totp_code,
)

PASSWORD = "Nexus!Mfa2026"


def _client(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'mfa-control-plane.db'}",
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


def _user(db, username: str, tenant_id: int, role: UserRole = UserRole.agent) -> User:
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


def _login(client: TestClient, username: str):
    return client.post("/api/auth/login", json={"username": username, "password": PASSWORD})


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_mfa_setup_login_replay_recovery_regeneration_and_disable(tmp_path):
    client, db, engine = _client(tmp_path)
    try:
        tenant = Tenant(tenant_key="mfa-tenant", display_name="MFA Tenant", is_active=True)
        db.add(tenant)
        db.flush()
        user = _user(db, "mfa_agent", tenant.id)
        db.commit()

        first_login = _login(client, user.username)
        assert first_login.status_code == 200, first_login.text
        first_token = first_login.json()["access_token"]

        begin = client.post(
            "/api/auth/mfa/setup/begin",
            headers=_auth(first_token),
            json={"current_password": PASSWORD},
        )
        assert begin.status_code == 200, begin.text
        secret = begin.json()["secret"]
        assert secret
        assert secret in begin.json()["otpauth_uri"]

        step = int(time.time() // 30)
        confirmed = client.post(
            "/api/auth/mfa/setup/confirm",
            headers=_auth(first_token),
            json={"code": totp_code(secret, step)},
        )
        assert confirmed.status_code == 200, confirmed.text
        recovery_codes = confirmed.json()["recovery_codes"]
        assert len(recovery_codes) == 10
        assert len(set(recovery_codes)) == 10
        assert client.get("/api/auth/me", headers=_auth(first_token)).status_code == 401

        policy = db.get(UserCredentialPolicy, user.id)
        assert policy is not None
        assert policy.mfa_enabled is True
        assert policy.mfa_secret_encrypted
        assert policy.mfa_secret_encrypted != secret
        assert secret not in (policy.mfa_recovery_codes_json or "")
        assert all(code not in (policy.mfa_recovery_codes_json or "") for code in recovery_codes)

        challenged = _login(client, user.username)
        assert challenged.status_code == 200
        assert challenged.json()["mfa_required"] is True
        assert "access_token" not in challenged.json()
        challenge_token = challenged.json()["challenge_token"]

        next_step_code = totp_code(secret, step + 1)
        verified = client.post(
            "/api/auth/mfa/login/verify",
            json={"challenge_token": challenge_token, "credential": next_step_code},
        )
        assert verified.status_code == 200, verified.text
        mfa_token = verified.json()["access_token"]
        assert verified.json()["user"]["mfa_enabled"] is True

        replay = client.post(
            "/api/auth/mfa/login/verify",
            json={"challenge_token": challenge_token, "credential": next_step_code},
        )
        assert replay.status_code == 401
        throttle = db.query(AuthThrottleEntry).first()
        assert throttle is not None
        assert throttle.fail_count >= 1

        status_response = client.get("/api/auth/mfa/status", headers=_auth(mfa_token))
        assert status_response.status_code == 200
        assert status_response.json()["enabled"] is True
        assert status_response.json()["recovery_codes_remaining"] == 10

        regenerated = client.post(
            "/api/auth/mfa/recovery-codes/regenerate",
            headers=_auth(mfa_token),
            json={
                "current_password": PASSWORD,
                "credential": recovery_codes[0],
            },
        )
        assert regenerated.status_code == 200, regenerated.text
        new_recovery_codes = regenerated.json()["recovery_codes"]
        assert len(new_recovery_codes) == 10
        assert set(new_recovery_codes).isdisjoint(set(recovery_codes))
        assert client.get("/api/auth/me", headers=_auth(mfa_token)).status_code == 401

        recovery_challenge = _login(client, user.username).json()["challenge_token"]
        recovery_login = client.post(
            "/api/auth/mfa/login/verify",
            json={
                "challenge_token": recovery_challenge,
                "credential": new_recovery_codes[0],
            },
        )
        assert recovery_login.status_code == 200, recovery_login.text
        recovery_token = recovery_login.json()["access_token"]

        repeated_challenge = _login(client, user.username).json()["challenge_token"]
        repeated_recovery = client.post(
            "/api/auth/mfa/login/verify",
            json={
                "challenge_token": repeated_challenge,
                "credential": new_recovery_codes[0],
            },
        )
        assert repeated_recovery.status_code == 401

        disabled = client.post(
            "/api/auth/mfa/disable",
            headers=_auth(recovery_token),
            json={
                "current_password": PASSWORD,
                "credential": new_recovery_codes[1],
            },
        )
        assert disabled.status_code == 200, disabled.text
        assert disabled.json()["reauthenticate"] is True
        assert client.get("/api/auth/me", headers=_auth(recovery_token)).status_code == 401

        direct_login = _login(client, user.username)
        assert direct_login.status_code == 200
        assert "access_token" in direct_login.json()
        assert direct_login.json()["user"]["mfa_enabled"] is False

        audit_text = "\n".join(
            f"{row.old_value_json or ''}\n{row.new_value_json or ''}"
            for row in db.query(AdminAuditLog).all()
        )
        assert secret not in audit_text
        for code in [*recovery_codes, *new_recovery_codes]:
            assert code not in audit_text
    finally:
        _close(db, engine)


def test_admin_reset_mfa_is_tenant_scoped_and_revokes_access(tmp_path):
    client, db, engine = _client(tmp_path)
    try:
        tenant_a = Tenant(tenant_key="mfa-admin-a", display_name="MFA Admin A", is_active=True)
        tenant_b = Tenant(tenant_key="mfa-admin-b", display_name="MFA Admin B", is_active=True)
        db.add_all([tenant_a, tenant_b])
        db.flush()
        admin = _user(db, "mfa_admin", tenant_a.id, UserRole.admin)
        target = _user(db, "mfa_target", tenant_a.id)
        outsider = _user(db, "mfa_outsider", tenant_b.id)
        db.commit()

        target_policy, secret, _uri = begin_mfa_setup(db, target)
        step = int(time.time() // 30)
        confirm_mfa_setup(db, target.id, totp_code(secret, step))
        db.commit()
        assert target_policy.mfa_enabled is True

        admin_headers = _auth(create_access_token(admin.id, admin.updated_at))
        target_token = create_access_token(target.id, target.updated_at)

        self_reset = client.post(
            f"/api/admin/identity/users/{admin.id}/reset-mfa",
            headers=admin_headers,
        )
        assert self_reset.status_code == 400

        cross_tenant = client.post(
            f"/api/admin/identity/users/{outsider.id}/reset-mfa",
            headers=admin_headers,
        )
        assert cross_tenant.status_code == 404

        reset = client.post(
            f"/api/admin/identity/users/{target.id}/reset-mfa",
            headers=admin_headers,
        )
        assert reset.status_code == 200, reset.text
        assert reset.json()["sessions_revoked"] is True
        assert client.get("/api/auth/me", headers=_auth(target_token)).status_code == 401

        db.refresh(target_policy)
        assert target_policy.mfa_enabled is False
        assert target_policy.mfa_secret_encrypted is None
        assert target_policy.mfa_recovery_codes_json is None
        audit = db.query(AdminAuditLog).filter(
            AdminAuditLog.action == "user.mfa_reset",
            AdminAuditLog.target_id == target.id,
        ).one()
        assert "secret" not in str(audit.new_value_json or "").lower()
    finally:
        _close(db, engine)
