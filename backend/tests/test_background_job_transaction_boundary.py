from __future__ import annotations

import ast
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.enums import JobStatus
from app.models import BackgroundJob
from app.services import background_jobs
from app.services.background_job_transaction_boundary import (
    dispatch_pending_background_jobs,
    dispatch_pending_webchat_ai_reply_jobs,
)
from app.utils.time import utc_now


class _FakeQuery:
    def __init__(self, db):
        self._db = db

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._db.current_recovery_row


class _FakeDB:
    def __init__(self, rows):
        self.rows = {row.id: row for row in rows}
        self.current_recovery_row = rows[0] if rows else None
        self.commits = 0
        self.rollbacks = 0
        self.fail_next_commit = False

    def query(self, model):
        return _FakeQuery(self)

    def commit(self):
        if self.fail_next_commit:
            self.fail_next_commit = False
            raise RuntimeError("commit deadlock")
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _job(job_id: int, *, job_type: str = background_jobs.AUTO_REPLY_JOB) -> SimpleNamespace:
    return SimpleNamespace(
        id=job_id,
        job_type=job_type,
        queue_name="default",
        status=JobStatus.processing,
        attempt_count=0,
        max_attempts=3,
        locked_at="locked",
        locked_by="worker-test",
        next_run_at=None,
        last_error=None,
        updated_at=None,
    )


def _assert_thin_delegates(
    path: Path,
    *,
    names: tuple[str, ...],
    authority_module: str,
) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    for name in names:
        node = functions[name]
        assert len(node.body) == 2, name
        import_node, return_node = node.body
        assert isinstance(import_node, ast.ImportFrom), name
        assert import_node.module == authority_module, name
        assert isinstance(return_node, ast.Return), name
        assert isinstance(return_node.value, ast.Call), name
        segment = ast.get_source_segment(source, node) or ""
        for forbidden in (
            "claim_pending_",
            "process_background_job(",
            "process_outbound_message(",
            "db.commit()",
            "for job in",
            "for message in",
        ):
            assert forbidden not in segment, (name, forbidden)


def test_worker_runtime_selects_boundaries_without_package_monkey_patch():
    root = Path(__file__).resolve().parents[2]
    services_init = (root / "backend/app/services/__init__.py").read_text(
        encoding="utf-8"
    )
    runner = (root / "backend/scripts/run_worker.py").read_text(encoding="utf-8")
    background_boundary = (
        root / "backend/app/services/background_job_transaction_boundary.py"
    ).read_text(encoding="utf-8")
    outbound_boundary = (
        root / "backend/app/services/outbound_dispatch_transaction_boundary.py"
    ).read_text(encoding="utf-8")

    assert "apply_background_job_transaction_boundary_patch" not in services_init
    assert "apply_outbound_dispatch_transaction_boundary_patch" not in services_init
    assert "apply_background_job_transaction_boundary_patch" not in background_boundary
    assert "apply_outbound_dispatch_transaction_boundary_patch" not in outbound_boundary
    assert "background_job_transaction_boundary" in runner
    assert "outbound_dispatch_transaction_boundary" in runner
    assert "_dispatch_pending_" not in runner
    assert (
        "from app.services.background_jobs import dispatch_pending_background_jobs"
        not in runner
    )
    assert "from app.services.message_dispatch import dispatch_pending_messages" not in runner

    _assert_thin_delegates(
        root / "backend/app/services/background_jobs.py",
        names=(
            "dispatch_pending_background_jobs",
            "dispatch_pending_webchat_ai_reply_jobs",
        ),
        authority_module="background_job_transaction_boundary",
    )
    _assert_thin_delegates(
        root / "backend/app/services/message_dispatch.py",
        names=("dispatch_pending_messages",),
        authority_module="outbound_dispatch_transaction_boundary",
    )


def test_dispatch_pending_background_jobs_recovers_one_failed_attempt_and_continues(monkeypatch):
    first = _job(1)
    second = _job(2)
    db = _FakeDB([first, second])
    processed_ids: list[int] = []

    monkeypatch.setattr(background_jobs.settings, "email_mailbox_sync_enabled", False)
    monkeypatch.setattr(
        background_jobs,
        "claim_pending_jobs",
        lambda db, limit=None, worker_id=None, job_types=None: [first, second],
    )

    def fake_process(db_arg, job):
        processed_ids.append(job.id)
        if job.id == 1:
            db.current_recovery_row = first
            raise RuntimeError("job exploded")
        job.status = JobStatus.done
        job.locked_at = None
        job.locked_by = None
        return job

    monkeypatch.setattr(background_jobs, "process_background_job", fake_process)

    processed = dispatch_pending_background_jobs(
        db,
        worker_id="worker-test",
    )

    assert processed_ids == [1, 2]
    assert [row.id for row in processed] == [1, 2]
    assert db.rollbacks == 1
    assert db.commits == 2
    assert first.status == JobStatus.pending
    assert first.attempt_count == 1
    assert first.last_error == "Unhandled background job exception: RuntimeError"
    assert first.locked_at is None
    assert first.locked_by is None
    assert second.status == JobStatus.done


def test_dispatch_pending_background_jobs_marks_dead_when_recovered_attempt_exhausts_retries(monkeypatch):
    row = _job(7)
    row.attempt_count = 2
    row.max_attempts = 3
    db = _FakeDB([row])
    db.current_recovery_row = row

    monkeypatch.setattr(background_jobs.settings, "email_mailbox_sync_enabled", False)
    monkeypatch.setattr(
        background_jobs,
        "claim_pending_jobs",
        lambda db, limit=None, worker_id=None, job_types=None: [row],
    )
    monkeypatch.setattr(
        background_jobs,
        "process_background_job",
        lambda db, job: (_ for _ in ()).throw(RuntimeError("last retry failed")),
    )

    processed = dispatch_pending_background_jobs(
        db,
        worker_id="worker-test",
    )

    assert [item.id for item in processed] == [7]
    assert db.rollbacks == 1
    assert db.commits == 1
    assert row.status == JobStatus.dead
    assert row.attempt_count == 3
    assert row.next_run_at is None



def test_dispatch_pending_webchat_ai_jobs_uses_attempt_boundary(monkeypatch):
    row = _job(11, job_type=background_jobs.WEBCHAT_AI_REPLY_JOB)
    db = _FakeDB([row])
    db.current_recovery_row = row

    monkeypatch.setattr(
        background_jobs,
        "claim_pending_jobs",
        lambda db, limit=None, worker_id=None, job_types=None: [row],
    )
    monkeypatch.setattr(
        background_jobs,
        "process_background_job",
        lambda db, job: (_ for _ in ()).throw(RuntimeError("webchat job failed")),
    )

    processed = dispatch_pending_webchat_ai_reply_jobs(
        db,
        worker_id="worker-webchat-ai-test",
    )

    assert [item.id for item in processed] == [11]
    assert db.rollbacks == 1
    assert db.commits == 1
    assert row.status == JobStatus.pending
    assert row.attempt_count == 1
    assert row.locked_at is None
    assert row.locked_by is None


def test_attempt_boundary_recovers_commit_failure(monkeypatch):
    row = _job(12, job_type=background_jobs.WEBCHAT_AI_REPLY_JOB)
    db = _FakeDB([row])
    db.current_recovery_row = row
    db.fail_next_commit = True

    monkeypatch.setattr(
        background_jobs,
        "claim_pending_jobs",
        lambda db, limit=None, worker_id=None, job_types=None: [row],
    )

    def fake_process(db_arg, job):
        job.status = JobStatus.done
        job.locked_at = None
        job.locked_by = None
        return job

    monkeypatch.setattr(background_jobs, "process_background_job", fake_process)

    processed = dispatch_pending_webchat_ai_reply_jobs(
        db,
        worker_id="worker-webchat-ai-test",
    )

    assert [item.id for item in processed] == [12]
    assert db.rollbacks == 1
    assert db.commits == 1
    assert row.status == JobStatus.pending
    assert row.attempt_count == 1
    assert row.last_error == "Unhandled background job exception: RuntimeError"


def test_claim_pending_jobs_reclaims_stale_processing_job(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'jobs.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    SessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        future=True,
        expire_on_commit=False,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(background_jobs.settings, "job_lock_seconds", 60)

    stale_time = utc_now() - timedelta(minutes=10)
    with SessionLocal() as db:
        row = BackgroundJob(
            queue_name="webchat_ai_reply",
            job_type=background_jobs.WEBCHAT_AI_REPLY_JOB,
            payload_json="{}",
            status=JobStatus.processing,
            locked_at=stale_time,
            locked_by="dead-worker",
            dedupe_key="webchat-ai-turn:test",
        )
        db.add(row)
        db.commit()

        claimed = background_jobs.claim_pending_jobs(
            db,
            limit=1,
            worker_id="new-worker",
            job_types=[background_jobs.WEBCHAT_AI_REPLY_JOB],
        )

        assert [job.id for job in claimed] == [row.id]
        assert claimed[0].status == JobStatus.processing
        assert claimed[0].locked_by == "new-worker"
        assert claimed[0].locked_at > stale_time

    engine.dispose()
