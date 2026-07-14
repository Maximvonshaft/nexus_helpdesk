from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/operator_queue_current_scopes.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.operator_queue import get_current_operator_queue_scopes
from app.db import Base
from app.enums import UserRole
from app.model_registry import register_all_models
from app.models import Market, Team, User
from app.operator_schemas import (
    OperatorQueueCurrentScopesResponse,
    OperatorQueueScopeGrantUpsert,
)
from app.services.operator_queue_scope import list_current_scope_grants, upsert_scope_grant

register_all_models()


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'operator_queue_current_scopes.db'}",
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


def _user(db, *, username: str, role: UserRole, team_id: int | None = None) -> User:
    row = User(
        username=username,
        display_name=username,
        email=f"{username}@example.test",
        password_hash="x",
        role=role,
        team_id=team_id,
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def _team(db, *, code: str, country: str) -> Team:
    market = Market(code=code, name=f"Market {country}", country_code=country, is_active=True)
    db.add(market)
    db.flush()
    team = Team(name=f"Team {country}", market_id=market.id, is_active=True)
    db.add(team)
    db.flush()
    return team


def _grant(
    db,
    *,
    admin: User,
    user: User,
    tenant: str,
    country: str,
    channel: str,
    enabled: bool = True,
):
    return upsert_scope_grant(
        db,
        current_user=admin,
        payload=OperatorQueueScopeGrantUpsert(
            user_id=user.id,
            tenant_key=tenant,
            country_code=country,
            channel_key=channel,
            enabled=enabled,
        ),
    )


def test_current_user_receives_only_own_active_grant_scopes(db_session):
    me_team = _team(db_session, code="ME", country="ME")
    admin = _user(db_session, username="admin", role=UserRole.admin)
    agent = _user(db_session, username="agent", role=UserRole.agent, team_id=me_team.id)
    other = _user(db_session, username="other", role=UserRole.manager)

    _grant(db_session, admin=admin, user=agent, tenant="tenant-me", country="ME", channel="webchat")
    _grant(db_session, admin=admin, user=agent, tenant="tenant-disabled", country="ME", channel="whatsapp", enabled=False)
    _grant(db_session, admin=admin, user=agent, tenant="tenant-wrong-country", country="CH", channel="webchat")
    _grant(db_session, admin=admin, user=other, tenant="tenant-other-user-secret", country="ME", channel="webchat")
    db_session.commit()

    result = list_current_scope_grants(db_session, current_user=agent)
    validated = OperatorQueueCurrentScopesResponse.model_validate(result)

    assert [(item.tenant_key, item.country_code, item.channel_key) for item in validated.items] == [
        ("tenant-wrong-country", "CH", "webchat"),
        ("tenant-me", "ME", "webchat"),
    ]
    serialized = validated.model_dump_json()
    assert "tenant-disabled" not in serialized
    assert "tenant-wrong-country" in serialized
    assert "tenant-other-user-secret" not in serialized
    assert "user_id" not in serialized
    assert "grant_id" not in serialized


def test_manager_receives_each_active_scope_owned_by_that_manager(db_session):
    admin = _user(db_session, username="admin", role=UserRole.admin)
    manager = _user(db_session, username="manager", role=UserRole.manager)
    _grant(db_session, admin=admin, user=manager, tenant="tenant-a", country="CH", channel="webchat")
    _grant(db_session, admin=admin, user=manager, tenant="tenant-b", country="ME", channel="whatsapp")
    db_session.commit()

    result = OperatorQueueCurrentScopesResponse.model_validate(
        get_current_operator_queue_scopes(db=db_session, current_user=manager)
    )

    assert [(item.country_code, item.channel_key, item.tenant_key) for item in result.items] == [
        ("CH", "webchat", "tenant-a"),
        ("ME", "whatsapp", "tenant-b"),
    ]


def test_admin_without_explicit_scope_is_not_given_a_guessed_tenant(db_session):
    admin = _user(db_session, username="admin", role=UserRole.admin)
    db_session.commit()

    result = OperatorQueueCurrentScopesResponse.model_validate(
        get_current_operator_queue_scopes(db=db_session, current_user=admin)
    )

    assert result.items == []


def test_admin_explicit_scope_can_drive_the_same_selector_without_broad_inventory(db_session):
    grantor = _user(db_session, username="grantor", role=UserRole.admin)
    admin = _user(db_session, username="scoped-admin", role=UserRole.admin)
    _grant(db_session, admin=grantor, user=admin, tenant="tenant-admin", country="CH", channel="webchat")
    db_session.commit()

    result = OperatorQueueCurrentScopesResponse.model_validate(
        get_current_operator_queue_scopes(db=db_session, current_user=admin)
    )

    assert len(result.items) == 1
    assert result.items[0].tenant_key == "tenant-admin"
