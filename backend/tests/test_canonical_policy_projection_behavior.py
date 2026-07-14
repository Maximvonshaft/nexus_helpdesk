from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/canonical_policy_projection_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.enums import UserRole
from app.model_registry import register_all_models
from app.models import User, UserCapabilityOverride
from app.operator_models import OperatorQueueScopeGrant
from app.services.operator_queue_scope import (
    authorize_operator_scope,
    list_current_scope_grants,
    scope_grant_version,
)

register_all_models()

TENANT = "tenant-policy-a"
COUNTRY = "ME"
CHANNEL = "webchat"


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'canonical_policy_projection.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _user(db, *, username: str, role: UserRole) -> User:
    row = User(
        username=username,
        display_name=username,
        email=f"{username}@example.test",
        password_hash="x",
        role=role,
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def _grant(
    db,
    *,
    user: User,
    tenant: str = TENANT,
    country: str = COUNTRY,
    channel: str = CHANNEL,
    enabled: bool = True,
) -> OperatorQueueScopeGrant:
    row = OperatorQueueScopeGrant(
        user_id=user.id,
        tenant_key=tenant,
        country_code=country,
        channel_key=channel,
        enabled=enabled,
        granted_by=user.id,
    )
    db.add(row)
    db.flush()
    return row


def _authorize(db, user: User, *, tenant: str = TENANT, country: str = COUNTRY, channel: str = CHANNEL):
    return authorize_operator_scope(
        db,
        current_user=user,
        tenant_key=tenant,
        country_code=country,
        channel_key=channel,
    )


def test_admin_without_active_grant_cannot_bypass_normal_queue_scope(db_session) -> None:
    admin = _user(db_session, username="admin-no-grant", role=UserRole.admin)

    with pytest.raises(HTTPException) as exc:
        _authorize(db_session, admin)

    assert exc.value.status_code == 403
    assert exc.value.detail == "operator_queue_scope_not_granted"


@pytest.mark.parametrize(
    "role",
    [UserRole.admin, UserRole.manager, UserRole.lead, UserRole.agent, UserRole.auditor],
)
def test_normal_queue_authority_is_capability_plus_active_grant_not_role(db_session, role: UserRole) -> None:
    user = _user(db_session, username=f"granted-{role.value}", role=role)
    grant = _grant(db_session, user=user)

    tenant, country, channel, resolved = _authorize(db_session, user)

    assert (tenant, country, channel) == (TENANT, COUNTRY, CHANNEL)
    assert resolved.id == grant.id


def test_disabled_wrong_user_and_wrong_scope_grants_fail_closed(db_session) -> None:
    user = _user(db_session, username="scope-owner", role=UserRole.manager)
    other = _user(db_session, username="scope-other", role=UserRole.manager)
    disabled = _grant(db_session, user=user, enabled=False)
    _grant(db_session, user=other)

    with pytest.raises(HTTPException) as disabled_exc:
        _authorize(db_session, user)
    assert disabled_exc.value.detail == "operator_queue_scope_not_granted"

    disabled.enabled = True
    db_session.flush()
    with pytest.raises(HTTPException) as wrong_tenant:
        _authorize(db_session, user, tenant="tenant-other")
    assert wrong_tenant.value.detail == "operator_queue_scope_not_granted"

    with pytest.raises(HTTPException) as wrong_channel:
        _authorize(db_session, user, channel="email")
    assert wrong_channel.value.detail == "operator_queue_scope_not_granted"


def test_grant_country_is_authoritative_without_team_or_role_inference(db_session) -> None:
    user = _user(db_session, username="country-grant", role=UserRole.agent)
    grant = _grant(db_session, user=user, country="CH")

    tenant, country, channel, resolved = _authorize(db_session, user, country="CH")

    assert (tenant, country, channel) == (TENANT, "CH", CHANNEL)
    assert resolved.id == grant.id


def test_capability_override_changes_scope_cursor_authority_fingerprint(db_session) -> None:
    user = _user(db_session, username="fingerprint-user", role=UserRole.manager)
    grant = _grant(db_session, user=user)
    before = scope_grant_version(grant, current_user=user)

    db_session.add(
        UserCapabilityOverride(
            user_id=user.id,
            capability="policy-projection-test.capability",
            allowed=True,
        )
    )
    db_session.flush()
    after = scope_grant_version(grant, current_user=user)

    assert before != after


def test_team_relationship_changes_scope_cursor_authority_fingerprint(db_session) -> None:
    user = _user(db_session, username="team-fingerprint-user", role=UserRole.agent)
    grant = _grant(db_session, user=user)
    user.team_id = 101
    before = scope_grant_version(grant, current_user=user)

    user.team_id = 202
    after = scope_grant_version(grant, current_user=user)

    assert before != after


def test_current_scope_projection_contains_only_active_current_user_grants(db_session) -> None:
    user = _user(db_session, username="projection-user", role=UserRole.auditor)
    other = _user(db_session, username="projection-other", role=UserRole.auditor)
    _grant(db_session, user=user, tenant="tenant-z", country="CH", channel="email")
    _grant(db_session, user=user, tenant="tenant-a", country="ME", channel="webchat")
    _grant(db_session, user=user, tenant="tenant-disabled", enabled=False)
    _grant(db_session, user=other, tenant="tenant-other")

    result = list_current_scope_grants(db_session, current_user=user)

    assert result == {
        "items": [
            {
                "tenant_key": "tenant-z",
                "tenant_hash": result["items"][0]["tenant_hash"],
                "country_code": "CH",
                "channel_key": "email",
            },
            {
                "tenant_key": "tenant-a",
                "tenant_hash": result["items"][1]["tenant_hash"],
                "country_code": "ME",
                "channel_key": "webchat",
            },
        ]
    }
    assert all(item["tenant_key"] not in {"tenant-disabled", "tenant-other"} for item in result["items"])
