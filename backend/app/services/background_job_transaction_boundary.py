from __future__ import annotations

import logging
from typing import Any, Iterable

_PATCHED = False
LOGGER = logging.getLogger(__name__)


def _exception_reason(exc: Exception) -> str:
    return f"Unhandled background job exception: {type(exc).__name__}"


def _recover_unhandled_background_job_exception(db: Any, *, job_id: int, exc: Exception):
    from . import background_jobs

    job = db.query(background_jobs.BackgroundJob).filter(background_jobs.BackgroundJob.id == job_id).first()
    if job is None:
        LOGGER.warning(
            "background_job_exception_recovery_missing_job",
            extra={"event_payload": {"job_id": job_id, "error_type": type(exc).__name__}},
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
                "next_status": job.status.value if hasattr(job.status, "value") else str(job.status),
            }
        },
    )
    return job


def _process_claimed_jobs_with_attempt_boundary(db: Any, jobs: Iterable[Any], *, sync_only: bool = False) -> list[Any]:
    from . import background_jobs

    processed: list[Any] = []
    for job in jobs:
        if sync_only and job.job_type != background_jobs.EXTERNAL_CHANNEL_SYNC_JOB:
            continue
        job_id = job.id
        try:
            background_jobs.process_background_job(db, job)
            db.commit()
        except Exception as exc:
            db.rollback()
            recovered = _recover_unhandled_background_job_exception(db, job_id=job_id, exc=exc)
            if recovered is not None:
                db.commit()
                processed.append(recovered)
            continue
        processed.append(job)
    return processed


def _dispatch_pending_background_jobs_with_attempt_boundary(db: Any, *, limit: int | None = None, worker_id: str | None = None) -> list[Any]:
    from . import background_jobs

    if background_jobs.settings.external_channel_sync_enabled:
        background_jobs.enqueue_stale_external_channel_sync_jobs(db, limit=background_jobs.settings.external_channel_sync_batch_size)
        db.commit()
    if background_jobs.settings.email_mailbox_sync_enabled:
        from .email_mailbox_polling_service import enqueue_due_email_mailbox_sync_jobs
        enqueue_due_email_mailbox_sync_jobs(
            db,
            interval_seconds=background_jobs.settings.email_mailbox_sync_interval_seconds,
            limit=background_jobs.settings.email_mailbox_sync_batch_size,
        )
        db.commit()
    claimed = background_jobs.claim_pending_jobs(
        db,
        limit=limit,
        worker_id=worker_id,
        job_types=[
            background_jobs.AUTO_REPLY_JOB,
            background_jobs.ATTACHMENT_PERSIST_JOB,
            background_jobs.WEBCHAT_AI_REPLY_JOB,
            background_jobs.WEBCHAT_HANDOFF_SNAPSHOT_JOB,
            background_jobs.SPEEDAF_WORK_ORDER_CREATE_JOB,
            background_jobs.SPEEDAF_ADDRESS_UPDATE_JOB,
            background_jobs.SPEEDAF_VOICE_CALLBACK_JOB,
            background_jobs.EMAIL_MAILBOX_SYNC_JOB,
        ],
    )
    return _process_claimed_jobs_with_attempt_boundary(db, claimed)


def _dispatch_pending_sync_jobs_with_attempt_boundary(db: Any, *, limit: int | None = None, worker_id: str | None = None) -> list[Any]:
    from . import background_jobs

    if background_jobs.settings.external_channel_sync_enabled:
        background_jobs.enqueue_stale_external_channel_sync_jobs(db, limit=background_jobs.settings.external_channel_sync_batch_size)
        db.commit()
    claimed = background_jobs.claim_pending_jobs(db, limit=limit, worker_id=worker_id, job_types=[background_jobs.EXTERNAL_CHANNEL_SYNC_JOB])
    return _process_claimed_jobs_with_attempt_boundary(db, claimed, sync_only=True)


def _dispatch_pending_webchat_ai_reply_jobs_with_attempt_boundary(db: Any, *, limit: int | None = None, worker_id: str | None = None) -> list[Any]:
    from . import background_jobs

    claimed = background_jobs.claim_pending_jobs(db, limit=limit, worker_id=worker_id, job_types=[background_jobs.WEBCHAT_AI_REPLY_JOB])
    return _process_claimed_jobs_with_attempt_boundary(db, claimed)


def apply_background_job_transaction_boundary_patch() -> None:
    global _PATCHED
    if _PATCHED:
        return

    from . import background_jobs

    background_jobs.dispatch_pending_background_jobs = _dispatch_pending_background_jobs_with_attempt_boundary
    background_jobs.dispatch_pending_sync_jobs = _dispatch_pending_sync_jobs_with_attempt_boundary
    background_jobs.dispatch_pending_webchat_ai_reply_jobs = _dispatch_pending_webchat_ai_reply_jobs_with_attempt_boundary
    _PATCHED = True
