from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/background_job_dedupe_idempotency.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.db import Base  # noqa: E402
from app.enums import JobStatus  # noqa: E402
from app.models import BackgroundJob  # noqa: E402
from app.services.background_jobs import enqueue_background_job  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "background_job_dedupe_idempotency.db"
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


def _enqueue(db_session, *, dedupe_key: str | None, payload: dict | None = None, job_type: str = "test.job") -> BackgroundJob:
    return enqueue_background_job(
        db_session,
        queue_name="tests",
        job_type=job_type,
        payload=payload or {"ok": True},
        dedupe_key=dedupe_key,
    )


def test_same_active_dedupe_key_returns_single_job(db_session):
    first = _enqueue(db_session, dedupe_key="job:1")
    second = _enqueue(db_session, dedupe_key="job:1")
    db_session.commit()

    assert first.id == second.id
    assert db_session.query(BackgroundJob).filter(BackgroundJob.dedupe_key == "job:1").count() == 1


def test_terminal_job_does_not_block_new_active_job(db_session):
    first = _enqueue(db_session, dedupe_key="job:2")
    first.status = JobStatus.done
    db_session.commit()

    second = _enqueue(db_session, dedupe_key="job:2")
    db_session.commit()

    assert first.id != second.id
    assert db_session.query(BackgroundJob).filter(BackgroundJob.dedupe_key == "job:2").count() == 2


def test_integrity_error_recovery_keeps_outer_transaction_intact(db_session, monkeypatch):
    existing = _enqueue(db_session, dedupe_key="job:race")
    db_session.commit()

    outer_job = _enqueue(db_session, dedupe_key=None, payload={"outer": True}, job_type="outer.job")

    import app.services.background_jobs as background_jobs

    original_find = background_jobs._find_active_dedupe_job
    calls = {"count": 0}

    def _race_find(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return None
        return original_find(*args, **kwargs)

    monkeypatch.setattr(background_jobs, "_find_active_dedupe_job", _race_find)

    recovered = _enqueue(db_session, dedupe_key="job:race")
    db_session.commit()

    assert recovered.id == existing.id
    assert outer_job.id is not None
    assert db_session.query(BackgroundJob).filter(BackgroundJob.id == outer_job.id).one().job_type == "outer.job"
    assert db_session.query(BackgroundJob).filter(BackgroundJob.dedupe_key == "job:race", BackgroundJob.status.in_(["pending", "processing"])).count() == 1
