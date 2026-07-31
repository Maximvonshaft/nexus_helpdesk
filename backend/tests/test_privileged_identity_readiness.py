from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.enums import UserRole
from app.model_registry import register_all_models
from app.models import User
from app.models_identity_policy import UserCredentialPolicy
from app.services.privileged_identity_readiness import (
    collect_privileged_identity_readiness,
)
from app.utils.time import utc_now

register_all_models()


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'privileged-readiness.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    Base.metadata.create_all(engine)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _admin(db_session) -> User:
    user = User(
        username="production-admin",
        display_name="Production Administrator",
        email="production-admin@example.test",
        password_hash="opaque-test-hash",
        role=UserRole.admin,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def test_privileged_identity_is_not_ready_before_governed_password_and_mfa(
    db_session,
):
    admin = _admin(db_session)
    policy = db_session.get(UserCredentialPolicy, admin.id)
    assert policy is not None
    policy.must_change_password = True
    db_session.flush()

    result = collect_privileged_identity_readiness(db_session)

    assert result["status"] == "not_ready"
    assert result["active_privileged_identities"] == 1
    assert result["compliant_privileged_identities"] == 0
    assert result["noncompliant_user_ids"] == [admin.id]
    assert {
        "privileged_password_rotation_pending",
        "privileged_password_policy_evidence_missing",
        "privileged_mfa_not_confirmed",
        "privileged_mfa_recovery_unavailable",
    }.issubset(set(result["reason_codes"]))


def test_privileged_identity_is_ready_only_after_password_and_mfa_completion(
    db_session,
):
    admin = _admin(db_session)
    policy = db_session.get(UserCredentialPolicy, admin.id)
    assert policy is not None
    now = utc_now()
    policy.must_change_password = False
    policy.password_changed_at = now
    policy.mfa_enabled = True
    policy.mfa_confirmed_at = now
    policy.mfa_recovery_codes_json = json.dumps(["recovery-code-hash"])
    db_session.flush()

    result = collect_privileged_identity_readiness(db_session)

    assert result["status"] == "ready"
    assert result["reason_codes"] == []
    assert result["active_privileged_identities"] == 1
    assert result["compliant_privileged_identities"] == 1
    assert result["noncompliant_user_ids"] == []
