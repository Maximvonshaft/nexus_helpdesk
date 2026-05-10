from __future__ import annotations

import json

from sqlalchemy.orm import Session

from .background_jobs import _mark_done, _mark_retry, claim_pending_jobs
from .webchat_handoff_snapshot_service import WEBCHAT_HANDOFF_SNAPSHOT_JOB, process_webchat_handoff_snapshot_job
from ..models import BackgroundJob


def process_webchat_handoff_snapshot_background_job(db: Session, job: BackgroundJob) -> BackgroundJob:
    payload = json.loads(job.payload_json or "{}")
    try:
        snapshot = payload.get("snapshot")
        if not isinstance(snapshot, dict):
            raise RuntimeError("webchat handoff snapshot job missing snapshot payload")
        process_webchat_handoff_snapshot_job(db, snapshot=snapshot)
        _mark_done(job)
        return job
    except Exception as exc:
        _mark_retry(job, str(exc))
        return job


def dispatch_pending_webchat_handoff_snapshot_jobs(
    db: Session,
    *,
    limit: int | None = None,
    worker_id: str | None = None,
) -> list[BackgroundJob]:
    claimed = claim_pending_jobs(
        db,
        limit=limit,
        worker_id=worker_id,
        job_types=[WEBCHAT_HANDOFF_SNAPSHOT_JOB],
    )
    processed: list[BackgroundJob] = []
    for job in claimed:
        process_webchat_handoff_snapshot_background_job(db, job)
        processed.append(job)
    db.commit()
    return processed
