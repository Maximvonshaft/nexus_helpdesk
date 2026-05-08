from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_event_isolation_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.db import Base  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.models import User  # noqa: E402
from app.services.operator_queue import create_operator_task, transition_operator_task  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "event_isolation.db"
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
    row = User(username="admin", display_name="Admin", email="admin@example.test", password_hash="x", role=UserRole.admin, is_active=True)
    db.add(row)
    db.flush()
    return row


def test_operator_assign_survives_safe_event_writer_failure(db_session, monkeypatch, caplog):
    admin = make_admin(db_session)
    task, _ = create_operator_task(
        db_session,
        source_type="webchat",
        source_id="wc_public",
        ticket_id=123,
        webchat_conversation_id=456,
        task_type="handoff",
        reason_code="customer_requested_human",
    )
    db_session.commit()

    def boom(*args, **kwargs):
        raise RuntimeError("raw secret token should not leak")

    monkeypatch.setattr("app.services.operator_queue.safe_write_webchat_event", boom)
    caplog.set_level("WARNING", logger="nexusdesk")

    transitioned = transition_operator_task(db_session, task_id=task.id, action="assign", actor_id=admin.id)

    assert transitioned.status == "assigned"
    assert transitioned.assignee_id == admin.id
    rendered_logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "operator_queue_webchat_event_write_failed" in rendered_logs
    assert "raw secret token should not leak" not in rendered_logs


def test_operator_resolve_and_drop_main_state_still_success_without_event_dependency(db_session):
    admin = make_admin(db_session)
    resolve_task, _ = create_operator_task(db_session, source_type="openclaw", source_id="u1", unresolved_event_id=1, task_type="bridge_unresolved")
    drop_task, _ = create_operator_task(db_session, source_type="openclaw", source_id="u2", unresolved_event_id=2, task_type="bridge_unresolved")
    db_session.commit()

    resolved = transition_operator_task(db_session, task_id=resolve_task.id, action="resolve", actor_id=admin.id)
    dropped = transition_operator_task(db_session, task_id=drop_task.id, action="drop", actor_id=admin.id)

    assert resolved.status == "resolved"
    assert dropped.status == "dropped"
    assert resolved.resolved_at is not None
    assert dropped.resolved_at is not None
