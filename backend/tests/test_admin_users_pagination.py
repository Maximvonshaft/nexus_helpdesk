from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/admin_users_pagination_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.api.admin_perf import list_admin_users_paginated  # noqa: E402
from app.db import Base  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.models import User, UserCapabilityOverride  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "admin_users.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False}, future=True)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def make_user(db, username: str, role=UserRole.agent, active: bool = True):
    row = User(
        username=username,
        display_name=username,
        email=f"{username}@example.test",
        password_hash="x",
        role=role,
        is_active=active,
    )
    db.add(row)
    db.flush()
    return row


def test_admin_users_default_limit_cursor_and_no_overlap(db_session):
    admin = make_user(db_session, "admin", UserRole.admin)
    for idx in range(120):
        make_user(db_session, f"agent{idx:03d}", UserRole.agent)
    db_session.commit()

    first = list_admin_users_paginated(db_session, current_user=admin)
    second = list_admin_users_paginated(db_session, current_user=admin, cursor=int(first["next_cursor"]))

    assert len(first["items"]) == 50
    assert first["has_more"] is True
    assert first["next_cursor"] is not None
    assert not {item["id"] for item in first["items"]} & {item["id"] for item in second["items"]}
    assert first["filters"]["limit"] == 50


def test_admin_users_limit_cap_and_capability_override_preload(db_session):
    admin = make_user(db_session, "admin", UserRole.admin)
    agent = make_user(db_session, "agent", UserRole.agent)
    db_session.add(UserCapabilityOverride(user_id=agent.id, capability="runtime.manage", allowed=True))
    db_session.commit()

    result = list_admin_users_paginated(db_session, current_user=admin, limit=500)

    assert result["filters"]["limit"] == 100
    serialized_agent = next(item for item in result["items"] if item["username"] == "agent")
    assert "runtime.manage" in serialized_agent["capabilities"]


def test_admin_users_legacy_is_bounded_and_inactive_contract(db_session):
    admin = make_user(db_session, "admin", UserRole.admin)
    inactive = make_user(db_session, "inactive", UserRole.agent, active=False)
    db_session.commit()

    modern = list_admin_users_paginated(db_session, current_user=admin)
    legacy = list_admin_users_paginated(db_session, current_user=admin, legacy=True)

    assert inactive.id not in {item["id"] for item in modern["items"]}
    assert inactive.id in {item["id"] for item in legacy}
    assert isinstance(legacy, list)
    assert len(legacy) <= 50


def test_admin_users_agent_forbidden(db_session):
    agent = make_user(db_session, "agent", UserRole.agent)
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        list_admin_users_paginated(db_session, current_user=agent)

    assert exc.value.status_code == 403
