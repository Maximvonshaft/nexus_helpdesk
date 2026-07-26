from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/operator_queue_terminal_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.db import Base  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.models import AdminAuditLog, User  # noqa: E402
from app.operator_models import OperatorTask  # noqa: E402
from app.services.operator_queue import (  # noqa: E402
    OperatorQueueError,
    create_operator_task,
    transition_operator_task,
)


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "operator_queue_terminal.db"
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


def make_admin(db):
    row = User(
        username="admin",
        display_name="admin",
        email="admin@example.test",
        password_hash="x",
        role=UserRole.admin,
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def make_task(db, *, status: str = "pending") -> OperatorTask:
    row = OperatorTask(
        source_type="control_tower",
        source_id=status,
        task_type="control_tower_action",
        status=status,
        priority=50,
    )
    db.add(row)
    db.flush()
    return row


def assert_terminal_error(exc):
    assert exc.value.status_code == 409
    assert exc.value.code == "operator_task_terminal"


def test_pending_projection_owned_task_assigns_to_assigned(db_session):
    admin = make_admin(db_session)
    row, _ = create_operator_task(
        db_session,
        source_type="control_tower",
        source_id="assign-unassigned",
        task_type="control_tower_action",
    )

    transitioned = transition_operator_task(
        db_session,
        task_id=row.id,
        action="assign",
        actor_id=admin.id,
    )

    assert transitioned.status == "assigned"
    assert transitioned.assignee_id == admin.id


def test_source_owned_handoff_is_rejected_before_any_audit(db_session):
    admin = make_admin(db_session)
    row, _ = create_operator_task(
        db_session,
        source_type="webchat_handoff",
        source_id="12",
        source_version=1,
        webchat_conversation_id=44,
        task_type="handoff",
    )
    before = db_session.query(AdminAuditLog).count()

    with pytest.raises(OperatorQueueError) as exc:
        transition_operator_task(
            db_session,
            task_id=row.id,
            action="assign",
            actor_id=admin.id,
        )

    assert exc.value.status_code == 409
    assert exc.value.code == "operator_task_projection_command_forbidden"
    assert db_session.query(AdminAuditLog).count() == before


def test_resolved_task_assign_returns_409_without_audit(db_session):
    admin = make_admin(db_session)
    task = make_task(db_session, status="resolved")
    before = db_session.query(AdminAuditLog).count()

    with pytest.raises(OperatorQueueError) as exc:
        transition_operator_task(
            db_session,
            task_id=task.id,
            action="assign",
            actor_id=admin.id,
        )

    assert_terminal_error(exc)
    assert db_session.query(AdminAuditLog).count() == before
    db_session.refresh(task)
    assert task.status == "resolved"


def test_dropped_task_resolve_returns_409(db_session):
    admin = make_admin(db_session)
    task = make_task(db_session, status="dropped")

    with pytest.raises(OperatorQueueError) as exc:
        transition_operator_task(
            db_session,
            task_id=task.id,
            action="resolve",
            actor_id=admin.id,
        )

    assert_terminal_error(exc)
    db_session.refresh(task)
    assert task.status == "dropped"


def test_missing_task_still_returns_404(db_session):
    admin = make_admin(db_session)

    with pytest.raises(OperatorQueueError) as exc:
        transition_operator_task(
            db_session,
            task_id=999,
            action="assign",
            actor_id=admin.id,
        )

    assert exc.value.status_code == 404
    assert exc.value.code == "operator_task_not_found"


def test_unsupported_action_still_returns_400(db_session):
    admin = make_admin(db_session)
    task = make_task(db_session, status="pending")

    with pytest.raises(OperatorQueueError) as exc:
        transition_operator_task(
            db_session,
            task_id=task.id,
            action="unsupported",
            actor_id=admin.id,
        )

    assert exc.value.status_code == 400
    assert exc.value.code == "unsupported_operator_task_action"
