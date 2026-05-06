from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/operator_queue_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.db import Base  # noqa: E402
from app.operator_models import OperatorTask  # noqa: E402
from app.services.operator_queue import create_operator_task, list_operator_tasks  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "operator_queue_pagination.db"
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


def _collect_pages(db_session, **filters):
    cursor = None
    ids = []
    while True:
        page = list_operator_tasks(db_session, limit=2, cursor=cursor, **filters)
        ids.extend([item["id"] for item in page["items"]])
        cursor = page["next_cursor"]
        if not cursor:
            break
    return ids


def test_priority_id_cursor_paginates_without_duplicates_or_gaps(db_session):
    priorities = [50, 40, 40, 60, 50]
    for idx, priority in enumerate(priorities):
        create_operator_task(db_session, source_type="webchat", task_type="handoff", source_id=f"wc-{idx}", priority=priority)
        db_session.flush()

    expected = [row.id for row in db_session.query(OperatorTask).order_by(OperatorTask.priority.asc(), OperatorTask.id.desc()).all()]
    actual = _collect_pages(db_session)

    assert actual == expected
    assert len(actual) == 5
    assert len(set(actual)) == 5


def test_filters_do_not_break_pagination(db_session):
    for idx in range(5):
        create_operator_task(
            db_session,
            source_type="openclaw" if idx % 2 else "webchat",
            task_type="bridge_unresolved" if idx % 2 else "handoff",
            source_id=f"src-{idx}",
            priority=40 + idx,
        )
    db_session.commit()

    expected = [
        row.id
        for row in db_session.query(OperatorTask)
        .filter(OperatorTask.source_type == "webchat", OperatorTask.task_type == "handoff", OperatorTask.status == "pending")
        .order_by(OperatorTask.priority.asc(), OperatorTask.id.desc())
        .all()
    ]
    actual = _collect_pages(db_session, status="pending", source_type="webchat", task_type="handoff")

    assert actual == expected
    assert len(actual) == 3
    assert len(set(actual)) == 3
