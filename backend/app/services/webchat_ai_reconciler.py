from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..enums import JobStatus
from ..models import BackgroundJob
from ..utils.time import utc_now
from ..webchat_models import WebchatAITurn, WebchatConversation
from .webchat_ai_turn_service import AI_TURN_TERMINAL_STATUSES, clear_active_ai_snapshot_if_current, write_webchat_event


def _status_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def reconcile_webchat_ai_state(db: Session, conversation_id: int | None = None) -> dict[str, int]:
    """Repair stale WebChat AI turn snapshots.

    The conversation active_ai_* fields are only a fast read cache. WebchatAITurn
    remains the source of truth. This reconciler prevents visitor widgets from
    showing typing dots forever after dead jobs, worker crashes, or stale cached
    snapshots.
    """
    query = db.query(WebchatConversation)
    if conversation_id is not None:
        query = query.filter(WebchatConversation.id == conversation_id)
    else:
        query = query.filter(WebchatConversation.active_ai_turn_id.is_not(None))

    inspected = 0
    cleared = 0
    failed = 0
    promoted = 0
    for conversation in query.all():
        inspected += 1
        turn = None
        if conversation.active_ai_turn_id:
            turn = db.query(WebchatAITurn).filter(WebchatAITurn.id == conversation.active_ai_turn_id).first()
        if turn is None:
            conversation.active_ai_turn_id = None
            conversation.active_ai_status = None
            conversation.active_ai_for_message_id = None
            conversation.active_ai_context_cutoff_message_id = None
            conversation.active_ai_started_at = None
            conversation.active_ai_updated_at = utc_now()
            cleared += 1
            continue

        if turn.status in AI_TURN_TERMINAL_STATUSES:
            clear_active_ai_snapshot_if_current(db, conversation=conversation, turn=turn)
            cleared += 1
            continue

        if turn.job_id:
            job = db.query(BackgroundJob).filter(BackgroundJob.id == turn.job_id).first()
            if job is not None and _status_value(job.status) == _status_value(JobStatus.dead):
                turn.status = "failed"
                turn.status_reason = job.last_error or "background_job_dead"
                turn.completed_at = utc_now()
                turn.updated_at = utc_now()
                clear_active_ai_snapshot_if_current(db, conversation=conversation, turn=turn)
                write_webchat_event(
                    db,
                    conversation_id=conversation.id,
                    ticket_id=turn.ticket_id,
                    event_type="ai_turn.failed",
                    payload={"ai_turn_id": turn.id, "reason": turn.status_reason, "job_id": job.id},
                )
                failed += 1
                continue

        if conversation.active_ai_status != turn.status:
            conversation.active_ai_status = turn.status
            conversation.active_ai_for_message_id = turn.latest_visitor_message_id or turn.trigger_message_id
            conversation.active_ai_context_cutoff_message_id = turn.context_cutoff_message_id
            conversation.active_ai_updated_at = utc_now()
            promoted += 1

    db.flush()
    return {"inspected": inspected, "cleared": cleared, "failed": failed, "promoted": promoted}
