from __future__ import annotations

import logging
import threading
import uuid
from contextlib import contextmanager
from typing import Any, Iterable, Iterator

from sqlalchemy import text, update

LOGGER = logging.getLogger(__name__)


def _exception_reason(exc: Exception) -> str:
    return f"Unhandled background job exception: {type(exc).__name__}"


def _is_sqlalchemy_session(db: Any) -> bool:
    return hasattr(db, "execute") and getattr(db, "bind", None) is not None


def _claim_token(worker_id: str | None) -> str:
    prefix = (worker_id or "job-worker").strip() or "job-worker"
    return f"{prefix[:80]}:{uuid.uuid4().hex}"


def _job_engine(db: Any):
    bind = db.get_bind() if hasattr(db, "get_bind") else getattr(db, "bind", None)
    return getattr(bind, "engine", bind) if bind is not None else None


def _refresh_job_lease(db: Any, *, job_id: int, lease_token: str) -> bool:
    if not _is_sqlalchemy_session(db):
        return True
    from . import background_jobs

    now = background_jobs.utc_now()
    result = db.execute(
        update(background_jobs.BackgroundJob)
        .where(
            background_jobs.BackgroundJob.id == job_id,
            background_jobs.BackgroundJob.status
            == background_jobs.JobStatus.processing,
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


def _heartbeat_job_lease(engine: Any, *, job_id: int, lease_token: str) -> bool:
    from . import background_jobs

    with engine.begin() as connection:
        result = connection.execute(
            update(background_jobs.BackgroundJob)
            .where(
                background_jobs.BackgroundJob.id == job_id,
                background_jobs.BackgroundJob.status
                == background_jobs.JobStatus.processing,
                background_jobs.BackgroundJob.locked_by == lease_token,
            )
            .values(
                locked_at=background_jobs.utc_now(),
                updated_at=background_jobs.utc_now(),
            )
        )
    return result.rowcount == 1


def _lease_heartbeat_interval_seconds() -> float:
    from . import background_jobs

    lock_seconds = max(3.0, float(background_jobs.settings.job_lock_seconds or 300))
    return max(1.0, min(30.0, lock_seconds / 3.0))


@contextmanager
def _job_lease_heartbeat(
    db: Any,
    *,
    job_id: int,
    lease_token: str,
) -> Iterator[None]:
    """Keep a claimed job authoritative while Provider or Tool I/O is in flight.

    Provider execution intentionally commits the application Session before
    external I/O. A separate short transaction therefore renews only the job
    lease, preventing a second Worker from reclaiming a healthy long-running
    attempt. No business data is written by the heartbeat thread.
    """

    if not _is_sqlalchemy_session(db):
        yield
        return
    engine = _job_engine(db)
    if engine is None:
        yield
        return

    stop = threading.Event()
    interval = _lease_heartbeat_interval_seconds()

    def run() -> None:
        while not stop.wait(interval):
            try:
                if not _heartbeat_job_lease(
                    engine,
                    job_id=job_id,
                    lease_token=lease_token,
                ):
                    LOGGER.warning(
                        "background_job_lease_heartbeat_rejected",
                        extra={"event_payload": {"job_id": job_id}},
                    )
                    return
            except Exception as exc:  # heartbeat failure must not hide job result
                LOGGER.warning(
                    "background_job_lease_heartbeat_failed",
                    extra={
                        "event_payload": {
                            "job_id": job_id,
                            "error_type": type(exc).__name__,
                        }
                    },
                )
                return

    thread = threading.Thread(
        target=run,
        name=f"job-lease-heartbeat-{job_id}",
        daemon=True,
    )
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=min(2.0, interval))


def commit_webchat_agent_provider_boundary(db: Any) -> None:
    """Persist bridge state and release database locks before Provider I/O."""

    if not _is_sqlalchemy_session(db):
        return
    db.commit()


def _owns_job_lease(db: Any, *, job_id: int, lease_token: str) -> bool:
    if not _is_sqlalchemy_session(db):
        return True
    from . import background_jobs

    engine = _job_engine(db)
    if engine is None:
        return False
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT locked_by, status "
                "FROM background_jobs WHERE id = :job_id"
            ),
            {"job_id": job_id},
        ).first()
    if row is None:
        return False
    locked_by, status = row[0], row[1]
    status_value = status.value if hasattr(status, "value") else str(status)
    return (
        locked_by == lease_token
        and status_value == background_jobs.JobStatus.processing.value
    )


def _recover_unhandled_background_job_exception(
    db: Any,
    *,
    job_id: int,
    lease_token: str,
    exc: Exception,
):
    from . import background_jobs
    from .webchat_ai_terminal_job_service import finalize_dead_webchat_ai_job

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
    job = db.get(background_jobs.BackgroundJob, job_id)
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
    finalize_dead_webchat_ai_job(db, job)
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


def _reject_execution_scope(
    db: Any,
    *,
    job_id: int,
    lease_token: str,
    exc: Exception,
):
    from . import background_jobs

    if not _owns_job_lease(db, job_id=job_id, lease_token=lease_token):
        db.rollback()
        return None
    job = db.get(background_jobs.BackgroundJob, job_id)
    if job is None:
        db.rollback()
        return None
    reason = f"background_job_execution_scope_rejected:{type(exc).__name__}"
    job.status = background_jobs.JobStatus.dead
    job.last_error = reason[:500]
    job.next_run_at = None
    job.locked_at = None
    job.locked_by = None
    job.updated_at = background_jobs.utc_now()
    db.commit()
    LOGGER.error(
        "background_job_execution_scope_rejected",
        extra={
            "event_payload": {
                "job_id": job.id,
                "job_type": job.job_type,
                "error_type": type(exc).__name__,
            }
        },
    )
    return job


def _process_claimed_jobs_with_attempt_boundary(
    db: Any,
    jobs: Iterable[Any],
    *,
    lease_token: str,
) -> list[Any]:
    from . import background_jobs
    from .background_job_execution_scope import (
        BackgroundJobExecutionScopeError,
        require_executable_background_job_scope,
    )
    from .data_subject_action_service import DataProcessingRestricted
    from .webchat_ai_terminal_job_service import finalize_dead_webchat_ai_job

    processed: list[Any] = []
    for job in jobs:
        job_id = job.id
        if not _refresh_job_lease(db, job_id=job_id, lease_token=lease_token):
            continue
        try:
            require_executable_background_job_scope(db, job)
        except (BackgroundJobExecutionScopeError, DataProcessingRestricted) as exc:
            rejected = _reject_execution_scope(
                db,
                job_id=job_id,
                lease_token=lease_token,
                exc=exc,
            )
            if rejected is not None:
                processed.append(rejected)
            continue
        try:
            with _job_lease_heartbeat(
                db,
                job_id=job_id,
                lease_token=lease_token,
            ):
                background_jobs.process_background_job(db, job)
                finalize_dead_webchat_ai_job(db, job)
            if not _owns_job_lease(
                db,
                job_id=job_id,
                lease_token=lease_token,
            ):
                db.rollback()
                LOGGER.warning(
                    "background_job_stale_completion_rejected",
                    extra={"event_payload": {"job_id": job_id}},
                )
                continue
            db.commit()
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
        processed.append(job)
    return processed


def _dispatch_realtime_control_work(
    db: Any,
    *,
    limit: int | None,
    worker_id: str | None,
) -> list[tuple[str, int]]:
    """Reuse the canonical Worker for durable voice and Provider-event work."""

    if not _is_sqlalchemy_session(db):
        return []
    from .telephony_event_service import reprocess_due_telephony_events
    from .voice_command_dispatcher import dispatch_pending_voice_commands

    bounded_limit = max(1, min(int(limit or 20), 100))
    command_ids = dispatch_pending_voice_commands(
        db,
        worker_id=(worker_id or "background-worker")[:120],
        limit=bounded_limit,
    )
    event_ids = reprocess_due_telephony_events(
        db,
        limit=bounded_limit,
    )
    return [
        *(("voice_command", int(command_id)) for command_id in command_ids),
        *(("telephony_event", int(event_id)) for event_id in event_ids),
    ]


def dispatch_pending_background_jobs(
    db: Any,
    *,
    limit: int | None = None,
    worker_id: str | None = None,
) -> list[Any]:
    """Dispatch all non-AI work owned by the one canonical background Worker."""

    from . import background_jobs
    from .background_job_execution_scope import claim_executable_background_jobs

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
    claimed = claim_executable_background_jobs(
        db,
        limit=limit,
        worker_id=lease_token,
        job_types={
            background_jobs.WEBCHAT_HANDOFF_SNAPSHOT_JOB,
            background_jobs.WEBCHAT_WHATSAPP_DELIVERY_JOB,
            background_jobs.SPEEDAF_WORK_ORDER_CREATE_JOB,
            background_jobs.SPEEDAF_ADDRESS_UPDATE_JOB,
            background_jobs.SPEEDAF_VOICE_CALLBACK_JOB,
            background_jobs.EMAIL_MAILBOX_SYNC_JOB,
        },
    )
    processed = _process_claimed_jobs_with_attempt_boundary(
        db,
        claimed,
        lease_token=lease_token,
    )
    processed.extend(
        _dispatch_realtime_control_work(
            db,
            limit=limit,
            worker_id=worker_id,
        )
    )
    return processed


def dispatch_pending_webchat_ai_reply_jobs(
    db: Any,
    *,
    limit: int | None = None,
    worker_id: str | None = None,
) -> list[Any]:
    from . import background_jobs
    from .background_job_execution_scope import claim_executable_background_jobs

    lease_token = _claim_token(worker_id)
    claimed = claim_executable_background_jobs(
        db,
        limit=limit,
        worker_id=lease_token,
        job_types={background_jobs.WEBCHAT_AI_REPLY_JOB},
    )
    return _process_claimed_jobs_with_attempt_boundary(
        db,
        claimed,
        lease_token=lease_token,
    )
