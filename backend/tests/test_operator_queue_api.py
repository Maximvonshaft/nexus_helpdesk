from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/operator_queue_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.api.operator_queue import get_operator_queue, project_operator_queue_endpoint  # noqa: E402
from app.db import Base  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.models import User  # noqa: E402
from app.operator_models import OperatorTask  # noqa: E402
from app.operator_schemas import OperatorTaskTransitionRequest  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "operator_queue_api.db"
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


def make_user(db, username="admin", role=UserRole.admin):
    row = User(username=username, display_name=username, email=f"{username}@example.test", password_hash="x", role=role, is_active=True)
    db.add(row)
    db.flush()
    return row


def test_get_queue_is_read_only(db_session):
    admin = make_user(db_session)
    before_tasks = db_session.query(OperatorTask).count()

    response = get_operator_queue(status=None, source_type=None, task_type=None, cursor=None, limit=50, db=db_session, current_user=admin)

    assert response["items"] == []
    assert db_session.query(OperatorTask).count() == before_tasks


def test_transition_request_extra_forbid():
    with pytest.raises(ValidationError):
        OperatorTaskTransitionRequest.model_validate({"note": "ok", "unexpected": "blocked"})


def test_runtime_manage_permission_required_for_get(db_session):
    agent = make_user(db_session, "agent", UserRole.agent)

    with pytest.raises(HTTPException) as exc:
        get_operator_queue(db=db_session, current_user=agent)

    assert exc.value.status_code == 403


def test_project_endpoint_requires_runtime_permission(db_session):
    agent = make_user(db_session, "agent2", UserRole.agent)

    with pytest.raises(HTTPException) as exc:
        project_operator_queue_endpoint(OperatorTaskTransitionRequest(note="try"), db_session, agent)

    assert exc.value.status_code == 403
