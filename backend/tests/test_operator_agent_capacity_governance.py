from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/nexus_operator_capacity_governance_tests.db",
)
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import (  # noqa: E402,F401
    models,
    models_agent_routing,
    models_control_plane,
    models_operations_dispatch,
    models_osr,
    operator_models,
    tool_models,
    voice_models,
    webchat_models,
)
from app.api.operator_agent_state import (  # noqa: E402
    AgentCapacityUpdateRequest,
    AgentStateUpdateRequest,
    get_managed_agent_state,
    update_agent_state,
    update_managed_agent_capacity,
)
from app.db import Base  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.models import User  # noqa: E402
from app.services.agent_routing_service import read_agent_state  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "operator_capacity_governance.db"
    engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        future=True,
        expire_on_commit=False,
    )
    Base.metadata.create_all(engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _user(db_session, *, suffix: str, role: UserRole) -> User:
    row = User(
        username=f"capacity-{suffix}",
        display_name=f"Capacity {suffix.title()}",
        password_hash="not-used",
        role=role,
        is_active=True,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_operator_cannot_raise_own_capacity_without_user_manage(db_session):
    agent = _user(db_session, suffix="agent", role=UserRole.agent)

    initial = update_agent_state(
        payload=AgentStateUpdateRequest(
            status="online",
            max_concurrent_conversations=3,
        ),
        db=db_session,
        current_user=agent,
    )
    assert initial["status"] == "online"
    assert initial["max_concurrent_conversations"] == 3

    with pytest.raises(HTTPException) as exc:
        update_agent_state(
            payload=AgentStateUpdateRequest(
                status="online",
                max_concurrent_conversations=7,
            ),
            db=db_session,
            current_user=agent,
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == "agent_capacity_update_requires_user_manage"
    assert read_agent_state(
        db_session,
        user_id=agent.id,
    )["max_concurrent_conversations"] == 3


def test_user_manager_updates_target_capacity_without_refreshing_presence(
    db_session,
):
    admin = _user(db_session, suffix="admin", role=UserRole.admin)
    agent = _user(db_session, suffix="managed-agent", role=UserRole.agent)

    changed = update_managed_agent_capacity(
        user_id=agent.id,
        payload=AgentCapacityUpdateRequest(
            max_concurrent_conversations=6,
        ),
        db=db_session,
        current_user=admin,
    )
    assert changed["max_concurrent_conversations"] == 6
    assert changed["status"] == "offline"
    assert changed["heartbeat_fresh"] is False
    assert changed["assignable"] is False
    assert changed["last_heartbeat_at"] is None

    managed = get_managed_agent_state(
        user_id=agent.id,
        db=db_session,
        current_user=admin,
    )
    assert managed["user_id"] == agent.id
    assert managed["username"] == agent.username
    assert managed["max_concurrent_conversations"] == 6
