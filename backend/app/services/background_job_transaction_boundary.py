from __future__ import annotations

import logging
import uuid
from contextlib import nullcontext
from typing import Any, Iterable

from sqlalchemy import update

LOGGER = logging.getLogger(__name__)


def _exception_reason(exc: Exception) -> str:
    return f"Unhandled background job exception: {type(exc).__name__}"


def _is_sqlalchemy_session(db: Any) -> bool:
    return hasattr(db, "execute") and getattr(db, "bind", None) is not None


def _claim_token(worker_id: str | None) -> str:
    prefix = (worker_id or "job-worker").strip() or "job-worker"
    return f"{prefix[:80]}:{uuid.uuid4().hex}"


def _refresh_job_lease(db: Any, *, job_id: int, lease_token: str) -> bool:
    """Renew only the attempt that still owns the processing row."""
    if not _is_sqlalchemy_session(db):
        return True

    from . import background_jobs

    now = background_jobs.utc_now()
    result = db.execute(
        update(background_jobs.BackgroundJob)
        .where(
            background_jobs.BackgroundJob.id == job_id,
            background_jobs.BackgroundJob.status == background_jobs.JobStatus.processing,
            background_jobs.BackgroundJob.locked_by == lease_token,
        )
        .values(locked_at=now, updated_at=now)
    )
    if result.rowcount != 1:
        db.rollback()
        LOGGER.warning(
            "background_job_lease_refresh_rejected",
            extra={"event_payload": {"job_id": job_id}},
        )
        return False
    db.commit()
    return True


def _owns_job_lease(db: Any, *, job_id: int, lease_token: str) -> bool:
    """Read the durable owner without flushing a possibly terminal ORM object."""
    if not _is_sqlalchemy_session(db):
        return True

    from . import background_jobs

    no_autoflush = getattr(db, "no_autoflush", nullcontext())
    with no_autoflush:
        row = (
            db.query(
                background_jobs.BackgroundJob.locked_by,
                background_jobs.BackgroundJob.status,
            )
            .filter(background_jobs.BackgroundJob.id == job_id)
            .first()
        )
    if row is None:
        return False
    locked_by = row[0]
    status = row[1]
    return (
        locked_by == lease_token
        and status == background_jobs.JobStatus.processing
    )


def _recover_unhandled_background_job_exception(
    db: Any,
    *,
    job_id: int,
    lease_token: str,
    exc: Exception,
):
    from . import background_jobs

    if not _owns_job_lease(db, job_id=job_id, lease_token=lease_token):
        LOGGER.warning(
            "background_job_stale_exception_result_rejected",
            extra={
                "event_payload": {
                    "job_id": job_id,
                    "error_type": type(exc).__name__,
                }
            },
        )
        return None

    job = (
        db.query(background_jobs.BackgroundJob)
        .filter(background_jobs.BackgroundJob.id == job_id)
        .first()
    )
    if job is None:
        LOGGER.warning(
            "background_job_exception_recovery_missing_job",
            extra={
                "event_payload": {
                    "job_id": job_id,
                    "error_type": type(exc).__name__,
                }
            },
        )
        return None

    background_jobs._mark_retry(job, _exception_reason(exc))
    LOGGER.warning(
        "background_job_attempt_exception_recovered",
        extra={
            "event_payload": {
                "job_id": job.id,
                "job_type": job.job_type,
                "queue_name": getattr(job, "queue_name", None),
                "error_type": type(exc).__name__,
                "attempt_count": getattr(job, "attempt_count", None),
                "next_status": (
                    job.status.value
                    if hasattr(job.status, "value")
                    else str(job.status)
                ),
            }
        },
    )
    return job



def _terminal_job_values(job: Any) -> dict[str, Any]:
    """Capture the bounded mutable state produced by one job attempt."""
    return {
        "status": job.status,
        "attempt_count": job.attempt_count,
        "next_run_at": job.next_run_at,
        "locked_at": job.locked_at,
        "locked_by": job.locked_by,
        "last_error": job.last_error,
        "updated_at": job.updated_at,
        "payload_json": job.payload_json,
    }


def _finalize_claimed_job(
    db: Any,
    *,
    job: Any,
    job_id: int,
    lease_token: str,
):
    """Commit an attempt only while its durable processing lease is still owned.

    Handlers mutate the ORM job into a terminal/retry state. For a real
    SQLAlchemy session we expire that dirty object, flush the handler's other
    writes, then apply the job transition with a conditional UPDATE against the
    original processing lease. This prevents a stale worker from committing
    either a success or retry transition after lease transfer.
    """
    if not _is_sqlalchemy_session(db):
        if not _owns_job_lease(db, job_id=job_id, lease_token=lease_token):
            return None
        db.commit()
        return job

    from . import background_jobs

    values = _terminal_job_values(job)
    # Discard the job object's terminal mutations before flushing other handler
    # writes. The terminal state is applied by the conditional lease update
    # below, while the original ORM identity remains attached for callers.
    db.expire(job)
    db.flush()
    result = db.execute(
        update(background_jobs.BackgroundJob)
        .where(
            background_jobs.BackgroundJob.id == job_id,
            background_jobs.BackgroundJob.status == background_jobs.JobStatus.processing,
            background_jobs.BackgroundJob.locked_by == lease_token,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        LOGGER.warning(
            "background_job_stale_completion_rejected",
            extra={"event_payload": {"job_id": job_id}},
        )
        return None
    db.commit()
    db.refresh(job)
    return job


def _process_claimed_jobs_with_attempt_boundary(
    db: Any,
    jobs: Iterable[Any],
    *,
    lease_token: str,
    sync_only: bool = False,
) -> list[Any]:
    from . import background_jobs

    processed: list[Any] = []
    for job in jobs:
        if sync_only and job.job_type != background_jobs.EXTERNAL_CHANNEL_SYNC_JOB:
            continue
        job_id = job.id
        if not _refresh_job_lease(db, job_id=job_id, lease_token=lease_token):
            continue
        try:
            background_jobs.process_background_job(db, job)
            finalized = _finalize_claimed_job(
                db,
                job=job,
                job_id=job_id,
                lease_token=lease_token,
            )
            if finalized is None:
                continue
        except Exception as exc:
            db.rollback()
            recovered = _recover_unhandled_background_job_exception(
                db,
                job_id=job_id,
                lease_token=lease_token,
                exc=exc,
            )
            if recovered is not None:
                db.commit()
                processed.append(recovered)
            continue
        processed.append(finalized)
    return processed


def dispatch_pending_background_jobs(
    db: Any,
    *,
    limit: int | None = None,
    worker_id: str | None = None,
) -> list[Any]:
    """Dispatch only the queues owned by the canonical background Worker."""
    from . import background_jobs

    if background_jobs.settings.email_mailbox_sync_enabled:
        from .email_mailbox_polling_service import enqueue_due_email_mailbox_sync_jobs

        enqueue_due_email_mailbox_sync_jobs(
            db,
            interval_seconds=(
                background_jobs.settings.email_mailbox_sync_interval_seconds
            ),
            limit=background_jobs.settings.email_mailbox_sync_batch_size,
        )
        db.commit()
    lease_token = _claim_token(worker_id)
    claimed = background_jobs.claim_pending_jobs(
        db,
        limit=limit,
        worker_id=lease_token,
        job_types=[
            background_jobs.AUTO_REPLY_JOB,
            background_jobs.ATTACHMENT_PERSIST_JOB,
            background_jobs.SPEEDAF_WORK_ORDER_CREATE_JOB,
            background_jobs.SPEEDAF_ADDRESS_UPDATE_JOB,
            background_jobs.SPEEDAF_VOICE_CALLBACK_JOB,
            background_jobs.EMAIL_MAILBOX_SYNC_JOB,
        ],
    )
    return _process_claimed_jobs_with_attempt_boundary(
        db,
        claimed,
        lease_token=lease_token,
    )


def dispatch_pending_sync_jobs(
    db: Any,
    *,
    limit: int | None = None,
    worker_id: str | None = None,
) -> list[Any]:
    from . import background_jobs

    if background_jobs.settings.external_channel_sync_enabled:
        background_jobs.enqueue_stale_external_channel_sync_jobs(
            db,
            limit=background_jobs.settings.external_channel_sync_batch_size,
        )
        db.commit()
    lease_token = _claim_token(worker_id)
    claimed = background_jobs.claim_pending_jobs(
        db,
        limit=limit,
        worker_id=lease_token,
        job_types=[background_jobs.EXTERNAL_CHANNEL_SYNC_JOB],
    )
    return _process_claimed_jobs_with_attempt_boundary(
        db,
        claimed,
        lease_token=lease_token,
        sync_only=True,
    )


def dispatch_pending_webchat_ai_reply_jobs(
    db: Any,
    *,
    limit: int | None = None,
    worker_id: str | None = None,
) -> list[Any]:
    from . import background_jobs

    lease_token = _claim_token(worker_id)
    claimed = background_jobs.claim_pending_jobs(
        db,
        limit=limit,
        worker_id=lease_token,
        job_types=[background_jobs.WEBCHAT_AI_REPLY_JOB],
    )
    return _process_claimed_jobs_with_attempt_boundary(
        db,
        claimed,
        lease_token=lease_token,
    )
