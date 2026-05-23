from __future__ import annotations

from sqlalchemy.orm import Session

from ..heartbeat_service import update_service_heartbeat
from ..observability import log_event, record_queue_snapshot, record_worker_poll
from .lifecycle import WebCallAIWorkerResult, claim_webcall_ai_sessions, release_webcall_ai_session


def run_webcall_ai_worker_once(
    db: Session,
    worker_id: str,
    limit: int = 10,
    noop_release: bool = True,
    lease_seconds: int = 30,
) -> dict[str, int]:
    record_worker_poll(worker_id)
    claimed_sessions = claim_webcall_ai_sessions(
        db,
        worker_id=worker_id,
        limit=limit,
        lease_seconds=lease_seconds,
    )
    released = 0
    failed = 0
    if noop_release:
        for session in claimed_sessions:
            try:
                if release_webcall_ai_session(
                    db,
                    session.id,
                    worker_id,
                    reason="pr2_noop_worker_cycle",
                ):
                    released += 1
            except Exception:
                failed += 1
                log_event(40, "webcall_ai_worker_release_failed", worker_id=worker_id, voice_session_id=session.id)

    result = WebCallAIWorkerResult(
        claimed=len(claimed_sessions),
        released=released,
        failed=failed,
        skipped=0 if claimed_sessions else 1,
    )
    update_service_heartbeat(
        db,
        service_name="webcall_ai_worker",
        instance_id=worker_id,
        status="ok",
        details=result.as_dict(),
    )
    db.commit()
    record_queue_snapshot("webcall_ai_worker", "processed", result.claimed)
    log_event(20, "webcall_ai_worker_cycle_complete", worker_id=worker_id, **result.as_dict())
    return result.as_dict()
