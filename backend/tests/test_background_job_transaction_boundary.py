from __future__ import annotations

from types import SimpleNamespace

from app.enums import JobStatus
from app.services import background_jobs
from app.services.background_job_transaction_boundary import apply_background_job_transaction_boundary_patch


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

    def query(self, model):
        return _FakeQuery(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _job(job_id: int, *, job_type: str = background_jobs.AUTO_REPLY_JOB) -> SimpleNamespace:
    return SimpleNamespace(
        id=job_id,
        job_type=job_type,
        queue_name='default',
        status=JobStatus.processing,
        attempt_count=0,
        max_attempts=3,
        locked_at='locked',
        locked_by='worker-test',
        next_run_at=None,
        last_error=None,
        updated_at=None,
    )


def test_dispatch_pending_background_jobs_recovers_one_failed_attempt_and_continues(monkeypatch):
    apply_background_job_transaction_boundary_patch()

    first = _job(1)
    second = _job(2)
    db = _FakeDB([first, second])
    processed_ids: list[int] = []

    monkeypatch.setattr(background_jobs.settings, 'openclaw_sync_enabled', False)
    monkeypatch.setattr(background_jobs.settings, 'email_mailbox_sync_enabled', False)
    monkeypatch.setattr(background_jobs, 'claim_pending_jobs', lambda db, limit=None, worker_id=None, job_types=None: [first, second])

    def fake_process(db_arg, job):
        processed_ids.append(job.id)
        if job.id == 1:
            db.current_recovery_row = first
            raise RuntimeError('job exploded')
        job.status = JobStatus.done
        job.locked_at = None
        job.locked_by = None
        return job

    monkeypatch.setattr(background_jobs, 'process_background_job', fake_process)

    processed = background_jobs.dispatch_pending_background_jobs(db, worker_id='worker-test')

    assert processed_ids == [1, 2]
    assert [row.id for row in processed] == [1, 2]
    assert db.rollbacks == 1
    assert db.commits == 2
    assert first.status == JobStatus.pending
    assert first.attempt_count == 1
    assert first.last_error == 'Unhandled background job exception: RuntimeError'
    assert first.locked_at is None
    assert first.locked_by is None
    assert second.status == JobStatus.done


def test_dispatch_pending_background_jobs_marks_dead_when_recovered_attempt_exhausts_retries(monkeypatch):
    apply_background_job_transaction_boundary_patch()

    row = _job(7)
    row.attempt_count = 2
    row.max_attempts = 3
    db = _FakeDB([row])
    db.current_recovery_row = row

    monkeypatch.setattr(background_jobs.settings, 'openclaw_sync_enabled', False)
    monkeypatch.setattr(background_jobs.settings, 'email_mailbox_sync_enabled', False)
    monkeypatch.setattr(background_jobs, 'claim_pending_jobs', lambda db, limit=None, worker_id=None, job_types=None: [row])
    monkeypatch.setattr(background_jobs, 'process_background_job', lambda db, job: (_ for _ in ()).throw(RuntimeError('last retry failed')))

    processed = background_jobs.dispatch_pending_background_jobs(db, worker_id='worker-test')

    assert [item.id for item in processed] == [7]
    assert db.rollbacks == 1
    assert db.commits == 1
    assert row.status == JobStatus.dead
    assert row.attempt_count == 3
    assert row.next_run_at is None


def test_dispatch_pending_sync_jobs_uses_same_attempt_boundary(monkeypatch):
    apply_background_job_transaction_boundary_patch()

    row = _job(9, job_type=background_jobs.OPENCLAW_SYNC_JOB)
    db = _FakeDB([row])

    monkeypatch.setattr(background_jobs.settings, 'openclaw_sync_enabled', False)
    monkeypatch.setattr(background_jobs.settings, 'email_mailbox_sync_enabled', False)
    monkeypatch.setattr(background_jobs, 'claim_pending_jobs', lambda db, limit=None, worker_id=None, job_types=None: [row])

    def fake_process(db_arg, job):
        job.status = JobStatus.done
        job.locked_at = None
        job.locked_by = None
        return job

    monkeypatch.setattr(background_jobs, 'process_background_job', fake_process)

    processed = background_jobs.dispatch_pending_sync_jobs(db, worker_id='worker-test')

    assert [item.id for item in processed] == [9]
    assert db.rollbacks == 0
    assert db.commits == 1
    assert row.status == JobStatus.done
