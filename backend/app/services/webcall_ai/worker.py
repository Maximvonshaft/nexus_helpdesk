from __future__ import annotations

from sqlalchemy.orm import Session

from ..heartbeat_service import update_service_heartbeat
from ..observability import log_event, record_queue_snapshot, record_worker_poll
from .lifecycle import (
    WebCallAIWorkerResult,
    claim_webcall_ai_sessions,
    fail_webcall_ai_session,
    heartbeat_webcall_ai_session,
    release_webcall_ai_session,
)
from .mock_turn_executor import execute_mock_turn_for_claimed_session


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
    turns = 0
    released = 0
    failed = 0
    if noop_release:
        for session in claimed_sessions:
            try:
                execute_mock_turn_for_claimed_session(db, session=session, worker_id=worker_id)
                turns += 1
                heartbeat_webcall_ai_session(db, session.id, worker_id, lease_seconds=lease_seconds)
                if release_webcall_ai_session(
                    db,
                    session.id,
                    worker_id,
                    reason="pr3_mock_turn_complete",
                ):
                    released += 1
            except Exception as exc:
                db.rollback()
                failed += 1
                fail_webcall_ai_session(
                    db,
                    session.id,
                    worker_id,
                    error_code="mock_turn_failed",
                    error_message=str(exc),
                )
                log_event(40, "webcall_ai_worker_mock_turn_failed", worker_id=worker_id, voice_session_id=session.id)

    result = WebCallAIWorkerResult(
        claimed=len(claimed_sessions),
        released=released,
        failed=failed,
        skipped=0 if claimed_sessions else 1,
    )
    result_dict = result.as_dict()
    result_dict["turns"] = turns
    update_service_heartbeat(
        db,
        service_name="webcall_ai_worker",
        instance_id=worker_id,
        status="ok",
        details=result_dict,
    )
    db.commit()
    record_queue_snapshot("webcall_ai_worker", "processed", result.claimed)
    log_event(20, "webcall_ai_worker_cycle_complete", worker_id=worker_id, **result_dict)
    return result_dict
