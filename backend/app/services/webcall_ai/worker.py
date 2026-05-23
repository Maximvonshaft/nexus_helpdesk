from __future__ import annotations

from sqlalchemy.orm import Session

from ..heartbeat_service import update_service_heartbeat
from ..observability import log_event, record_queue_snapshot, record_worker_poll
from .config import get_webcall_ai_settings
from .lifecycle import (
    WebCallAIWorkerResult,
    claim_webcall_ai_sessions,
    fail_webcall_ai_session,
    heartbeat_webcall_ai_session,
    release_webcall_ai_session,
)
from .mock_turn_executor import execute_mock_turn_for_claimed_session
from .participant_service import (
    ai_participant_identity,
    ensure_ai_participant_record,
    mark_ai_participant_joined,
    mark_ai_participant_left,
)
from .presence_client import get_webcall_ai_presence_client
from .room_client import get_webcall_ai_room_client


def run_webcall_ai_worker_once(
    db: Session,
    worker_id: str,
    limit: int = 10,
    noop_release: bool = True,
    lease_seconds: int = 30,
) -> dict[str, int]:
    record_worker_poll(worker_id)
    settings = get_webcall_ai_settings()
    claimed_sessions = claim_webcall_ai_sessions(
        db,
        worker_id=worker_id,
        limit=limit,
        lease_seconds=lease_seconds,
    )
    turns = 0
    stt_events = 0
    tts_events = 0
    released = 0
    failed = 0
    participants = 0
    participant_joins = 0
    participant_leaves = 0
    presence_joins = 0
    presence_leaves = 0
    presence_failures = 0
    transcript_segments = 0
    stt_runtime_failures = 0
    if noop_release:
        for session in claimed_sessions:
            presence_joined = False
            participant_identity = None
            presence_client = None
            try:
                if settings.room_presence_enabled:
                    room_client = get_webcall_ai_room_client(settings)
                    presence_client = get_webcall_ai_presence_client(settings)
                    participant_identity = ai_participant_identity(session, settings)
                    token = room_client.issue_ai_token(
                        session=session,
                        participant_identity=participant_identity,
                        ttl_seconds=settings.participant_token_ttl_seconds,
                    )
                    ensure_ai_participant_record(
                        db,
                        session=session,
                        worker_id=worker_id,
                        token=token,
                        settings=settings,
                    )
                    join_result = presence_client.join_no_media(
                        session=session,
                        participant_identity=participant_identity,
                        token=token,
                        timeout_ms=settings.room_presence_join_timeout_ms,
                    )
                    if not join_result.joined:
                        presence_failures += 1
                        raise RuntimeError("AI participant no-media presence join failed")
                    mark_ai_participant_joined(db, session=session, worker_id=worker_id, settings=settings)
                    presence_joined = True
                    presence_joins += 1
                elif settings.participant_enabled:
                    room_client = get_webcall_ai_room_client(settings)
                    participant_identity = ai_participant_identity(session, settings)
                    token = room_client.issue_ai_token(
                        session=session,
                        participant_identity=participant_identity,
                        ttl_seconds=settings.participant_token_ttl_seconds,
                    )
                    ensure_ai_participant_record(
                        db,
                        session=session,
                        worker_id=worker_id,
                        token=token,
                        settings=settings,
                    )
                    join_result = room_client.join(
                        session=session,
                        participant_identity=participant_identity,
                        token=token,
                    )
                    if not join_result.joined:
                        raise RuntimeError("AI participant fake room join failed")
                    mark_ai_participant_joined(db, session=session, worker_id=worker_id, settings=settings)
                    participants += 1
                    participant_joins += 1

                turn_result = execute_mock_turn_for_claimed_session(db, session=session, worker_id=worker_id)
                turns += 1
                stt_events += turn_result.stt_events
                tts_events += turn_result.tts_events
                transcript_segments += turn_result.transcript_segments
                heartbeat_webcall_ai_session(db, session.id, worker_id, lease_seconds=lease_seconds)
                if settings.room_presence_enabled:
                    leave_result = presence_client.leave(session=session, participant_identity=participant_identity)
                    if not leave_result.left:
                        presence_failures += 1
                        raise RuntimeError("AI participant no-media presence leave failed")
                    mark_ai_participant_left(
                        db,
                        session=session,
                        worker_id=worker_id,
                        reason="mock_turn_complete",
                        settings=settings,
                    )
                    presence_leaves += 1
                    presence_joined = False
                elif settings.participant_enabled:
                    leave_result = room_client.leave(session=session, participant_identity=participant_identity)
                    if not leave_result.left:
                        raise RuntimeError("AI participant fake room leave failed")
                    mark_ai_participant_left(
                        db,
                        session=session,
                        worker_id=worker_id,
                        reason="mock_turn_complete",
                        settings=settings,
                    )
                    participant_leaves += 1
                if release_webcall_ai_session(
                    db,
                    session.id,
                    worker_id,
                    reason="pr4_mock_media_turn_complete",
                ):
                    released += 1
            except Exception as exc:
                if settings.room_presence_enabled and presence_joined and presence_client is not None and participant_identity:
                    try:
                        presence_client.leave(session=session, participant_identity=participant_identity)
                        presence_leaves += 1
                    except Exception:
                        presence_failures += 1
                db.rollback()
                failed += 1
                if settings.stt_runtime_enabled:
                    stt_runtime_failures += 1
                fail_webcall_ai_session(
                    db,
                    session.id,
                    worker_id,
                    error_code="mock_turn_failed",
                    error_message=type(exc).__name__,
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
    result_dict["stt_events"] = stt_events
    result_dict["tts_events"] = tts_events
    if settings.participant_enabled:
        result_dict["participants"] = participants
        result_dict["participant_joins"] = participant_joins
        result_dict["participant_leaves"] = participant_leaves
    if settings.room_presence_enabled:
        result_dict["presence_joins"] = presence_joins
        result_dict["presence_leaves"] = presence_leaves
        result_dict["presence_failures"] = presence_failures
    if settings.stt_runtime_enabled:
        result_dict["transcript_segments"] = transcript_segments
        result_dict["stt_runtime_failures"] = stt_runtime_failures
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
