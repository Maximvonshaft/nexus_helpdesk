from __future__ import annotations

import json
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
from app.models import AdminAuditLog, OpenClawUnresolvedEvent, User  # noqa: E402
from app.operator_models import OperatorTask  # noqa: E402
from app.services.operator_queue import (  # noqa: E402
    OperatorQueueError,
    create_operator_task,
    replay_operator_task,
    transition_operator_task,
)


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "operator_queue_terminal.db"
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


def make_admin(db):
    row = User(username="admin", display_name="admin", email="admin@example.test", password_hash="x", role=UserRole.admin, is_active=True)
    db.add(row)
    db.flush()
    return row


def make_unresolved(db, status: str = "pending") -> OpenClawUnresolvedEvent:
    row = OpenClawUnresolvedEvent(
        source="default",
        session_key="session-key",
        event_type="message",
        recipient="recipient",
        source_chat_id="chat",
        preferred_reply_contact="contact",
        payload_json=json.dumps({"type": "message"}),
        status=status,
        replay_count=0,
        last_error="provider error",
    )
    db.add(row)
    db.flush()
    return row


def make_task(db, *, status: str = "pending", unresolved_event_id: int | None = None, source_type: str = "openclaw") -> OperatorTask:
    row = OperatorTask(
        source_type=source_type,
        source_id=str(unresolved_event_id or status),
        unresolved_event_id=unresolved_event_id,
        task_type="bridge_unresolved" if source_type == "openclaw" else "handoff",
        status=status,
        priority=50,
    )
    db.add(row)
    db.flush()
    return row


def assert_terminal_error(exc):
    assert exc.value.status_code == 409
    assert exc.value.code == "operator_task_terminal"


def test_pending_task_assigns_to_assigned(db_session):
    admin = make_admin(db_session)
    row, _ = create_operator_task(db_session, source_type="webchat", source_id="wc-1", task_type="handoff")

    transitioned = transition_operator_task(db_session, task_id=row.id, action="assign", actor_id=admin.id)

    assert transitioned.status == "assigned"
    assert transitioned.assignee_id == admin.id


def test_resolved_task_assign_returns_409_without_audit(db_session):
    admin = make_admin(db_session)
    task = make_task(db_session, status="resolved")
    before = db_session.query(AdminAuditLog).count()

    with pytest.raises(OperatorQueueError) as exc:
        transition_operator_task(db_session, task_id=task.id, action="assign", actor_id=admin.id)

    assert_terminal_error(exc)
    assert db_session.query(AdminAuditLog).count() == before
    db_session.refresh(task)
    assert task.status == "resolved"


def test_dropped_task_resolve_returns_409_without_source_change(db_session):
    admin = make_admin(db_session)
    event = make_unresolved(db_session, status="dropped")
    task = make_task(db_session, status="dropped", unresolved_event_id=event.id)

    with pytest.raises(OperatorQueueError) as exc:
        transition_operator_task(db_session, task_id=task.id, action="resolve", actor_id=admin.id)

    assert_terminal_error(exc)
    db_session.refresh(event)
    db_session.refresh(task)
    assert event.status == "dropped"
    assert task.status == "dropped"


def test_replayed_task_replay_returns_409(db_session):
    admin = make_admin(db_session)
    event = make_unresolved(db_session, status="replayed")
    task = make_task(db_session, status="replayed", unresolved_event_id=event.id)

    with pytest.raises(OperatorQueueError) as exc:
        replay_operator_task(db_session, task_id=task.id, actor_id=admin.id, replay_func=lambda db, *, row: True)

    assert_terminal_error(exc)


def test_replay_failed_task_replay_returns_409(db_session):
    admin = make_admin(db_session)
    event = make_unresolved(db_session, status="replay_failed")
    task = make_task(db_session, status="replay_failed", unresolved_event_id=event.id)

    with pytest.raises(OperatorQueueError) as exc:
        replay_operator_task(db_session, task_id=task.id, actor_id=admin.id, replay_func=lambda db, *, row: True)

    assert_terminal_error(exc)


def test_missing_task_still_returns_404(db_session):
    admin = make_admin(db_session)

    with pytest.raises(OperatorQueueError) as exc:
        transition_operator_task(db_session, task_id=999, action="assign", actor_id=admin.id)

    assert exc.value.status_code == 404
    assert exc.value.code == "operator_task_not_found"


def test_unsupported_action_still_returns_400(db_session):
    admin = make_admin(db_session)
    task = make_task(db_session, status="pending")

    with pytest.raises(OperatorQueueError) as exc:
        transition_operator_task(db_session, task_id=task.id, action="unsupported", actor_id=admin.id)

    assert exc.value.status_code == 400
    assert exc.value.code == "unsupported_operator_task_action"
