from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.enums import JobStatus
from app.model_registry import register_all_models
from app.models import BackgroundJob
from app.services import background_jobs
from app.services.background_job_transaction_boundary import _owns_job_lease
from app.services.background_jobs import claim_pending_jobs, enqueue_background_job
from app.settings import get_settings
from app.utils.time import utc_now

register_all_models()


@pytest.fixture(scope="module")
def session_factory():
    settings = get_settings()
    if not settings.is_postgres:
        pytest.skip("PostgreSQL qualification requires a PostgreSQL DATABASE_URL")
    engine = create_engine(
        settings.database_url,
        future=True,
        poolclass=NullPool,
    )
    Session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        future=True,
        expire_on_commit=False,
    )
    try:
        yield Session
    finally:
        engine.dispose()


def _cleanup(Session, prefix: str) -> None:  # noqa: ANN001
    with Session() as db:
        db.query(BackgroundJob).filter(
            BackgroundJob.dedupe_key.like(f"{prefix}%")
        ).delete(synchronize_session=False)
        db.commit()


def test_concurrent_postgres_claims_never_duplicate_a_job(session_factory) -> None:  # noqa: ANN001
    Session = session_factory
    prefix = f"resilience-claim-{uuid.uuid4().hex}"
    job_type = f"resilience.claim.{uuid.uuid4().hex}"
    worker_count = 4
    jobs_per_worker = 6
    barrier = threading.Barrier(worker_count)
    try:
        with Session() as db:
            for index in range(worker_count * jobs_per_worker):
                enqueue_background_job(
                    db,
                    queue_name="resilience",
                    job_type=job_type,
                    payload={"fixture": index},
                    dedupe_key=f"{prefix}:{index}",
                )
            db.commit()

        def claim(worker_index: int) -> list[int]:
            with Session() as db:
                barrier.wait(timeout=20)
                rows = claim_pending_jobs(
                    db,
                    limit=jobs_per_worker,
                    worker_id=f"resilience-worker-{worker_index}",
                    job_types=[job_type],
                )
                return [int(row.id) for row in rows]

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            claimed_batches = list(executor.map(claim, range(worker_count)))

        claimed = [job_id for batch in claimed_batches for job_id in batch]
        assert all(len(batch) == jobs_per_worker for batch in claimed_batches)
        assert len(claimed) == worker_count * jobs_per_worker
        assert len(set(claimed)) == len(claimed)

        with Session() as db:
            rows = (
                db.query(BackgroundJob)
                .filter(BackgroundJob.id.in_(claimed))
                .all()
            )
            assert len(rows) == len(claimed)
            assert all(row.status == JobStatus.processing for row in rows)
            assert len({row.locked_by for row in rows}) == worker_count
    finally:
        _cleanup(Session, prefix)


def test_concurrent_enqueue_keeps_one_active_dedupe_record(session_factory) -> None:  # noqa: ANN001
    Session = session_factory
    prefix = f"resilience-dedupe-{uuid.uuid4().hex}"
    dedupe_key = f"{prefix}:same"
    worker_count = 8
    barrier = threading.Barrier(worker_count)
    try:
        def enqueue(worker_index: int) -> int:
            with Session() as db:
                barrier.wait(timeout=20)
                row = enqueue_background_job(
                    db,
                    queue_name="resilience",
                    job_type="resilience.dedupe",
                    payload={"worker": worker_index},
                    dedupe_key=dedupe_key,
                )
                db.commit()
                return int(row.id)

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            returned_ids = list(executor.map(enqueue, range(worker_count)))

        with Session() as db:
            active = (
                db.query(BackgroundJob)
                .filter(
                    BackgroundJob.dedupe_key == dedupe_key,
                    BackgroundJob.status.in_(
                        [JobStatus.pending, JobStatus.processing]
                    ),
                )
                .all()
            )
            assert len(active) == 1
            assert set(returned_ids) == {int(active[0].id)}
    finally:
        _cleanup(Session, prefix)


def test_expired_processing_lock_is_reclaimed_after_worker_crash(session_factory) -> None:  # noqa: ANN001
    Session = session_factory
    prefix = f"resilience-crash-{uuid.uuid4().hex}"
    job_type = f"resilience.crash.{uuid.uuid4().hex}"
    try:
        with Session() as db:
            row = BackgroundJob(
                queue_name="resilience",
                job_type=job_type,
                payload_json="{}",
                dedupe_key=f"{prefix}:one",
                status=JobStatus.processing,
                attempt_count=0,
                max_attempts=3,
                locked_at=utc_now()
                - timedelta(
                    seconds=int(background_jobs.settings.job_lock_seconds) + 5
                ),
                locked_by="crashed-worker",
                created_at=utc_now() - timedelta(minutes=10),
                updated_at=utc_now() - timedelta(minutes=10),
            )
            db.add(row)
            db.commit()
            crashed_job_id = int(row.id)

        with Session() as db:
            claimed = claim_pending_jobs(
                db,
                limit=1,
                worker_id="recovery-worker",
                job_types=[job_type],
            )
            assert [int(item.id) for item in claimed] == [crashed_job_id]

        with Session() as db:
            recovered = db.get(BackgroundJob, crashed_job_id)
            assert recovered is not None
            assert recovered.status == JobStatus.processing
            assert recovered.locked_by == "recovery-worker"
            assert recovered.locked_at is not None
            assert (
                db.query(BackgroundJob)
                .filter(BackgroundJob.dedupe_key == f"{prefix}:one")
                .count()
                == 1
            )
    finally:
        _cleanup(Session, prefix)


def test_old_attempt_loses_fencing_authority_after_lease_transfer(session_factory) -> None:  # noqa: ANN001
    Session = session_factory
    prefix = f"resilience-fence-{uuid.uuid4().hex}"
    old_token = f"old-worker:{uuid.uuid4().hex}"
    new_token = f"new-worker:{uuid.uuid4().hex}"
    try:
        with Session() as db:
            row = BackgroundJob(
                queue_name="resilience",
                job_type="resilience.fence",
                payload_json="{}",
                dedupe_key=f"{prefix}:one",
                status=JobStatus.processing,
                attempt_count=0,
                max_attempts=3,
                locked_at=utc_now(),
                locked_by=old_token,
            )
            db.add(row)
            db.commit()
            job_id = int(row.id)

        with Session() as transfer_db:
            changed = transfer_db.execute(
                update(BackgroundJob)
                .where(
                    BackgroundJob.id == job_id,
                    BackgroundJob.locked_by == old_token,
                    BackgroundJob.status == JobStatus.processing,
                )
                .values(locked_by=new_token, locked_at=utc_now())
            )
            assert changed.rowcount == 1
            transfer_db.commit()

        with Session() as old_worker_db:
            assert (
                _owns_job_lease(
                    old_worker_db,
                    job_id=job_id,
                    lease_token=old_token,
                )
                is False
            )
            assert (
                _owns_job_lease(
                    old_worker_db,
                    job_id=job_id,
                    lease_token=new_token,
                )
                is True
            )
    finally:
        _cleanup(Session, prefix)
