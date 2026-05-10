from __future__ import annotations

from datetime import timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..enums import JobStatus
from ..models import BackgroundJob
from ..settings import get_settings
from ..utils.time import utc_now
from ..webchat_models import WebchatAITurn, WebchatConversation
from .webchat_ai_turn_service import (
    AI_TURN_TERMINAL_STATUSES,
    DEFAULT_BRIDGE_GRACE_SECONDS,
    DEFAULT_FALLBACK_TIMEOUT_SECONDS,
    DEFAULT_PROCESSING_TIMEOUT_SECONDS,
    DEFAULT_QUEUED_TIMEOUT_SECONDS,
    clear_active_ai_snapshot_if_current,
    mark_ai_turn_timeout,
    safe_write_webchat_event,
)

settings = get_settings()


def _status_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _timeout_seconds_for_status(turn: WebchatAITurn) -> int | None:
    status = str(turn.status or "")
    if status == "queued":
        return int(getattr(settings, "webchat_ai_queued_timeout_seconds", DEFAULT_QUEUED_TIMEOUT_SECONDS) or DEFAULT_QUEUED_TIMEOUT_SECONDS)
    if status == "processing":
        return int(getattr(settings, "webchat_ai_processing_timeout_seconds", DEFAULT_PROCESSING_TIMEOUT_SECONDS) or DEFAULT_PROCESSING_TIMEOUT_SECONDS)
    if status == "fallback_generating":
        return int(getattr(settings, "webchat_ai_fallback_timeout_seconds", DEFAULT_FALLBACK_TIMEOUT_SECONDS) or DEFAULT_FALLBACK_TIMEOUT_SECONDS)
    if status == "bridge_calling":
        bridge_timeout = int(getattr(settings, "openclaw_bridge_timeout_seconds", 20) or 20)
        grace = int(getattr(settings, "webchat_ai_bridge_timeout_grace_seconds", DEFAULT_BRIDGE_GRACE_SECONDS) or DEFAULT_BRIDGE_GRACE_SECONDS)
        return bridge_timeout + grace
    return None


def _turn_anchor_time(turn: WebchatAITurn):
    return turn.updated_at or turn.started_at or turn.created_at


def _ensure_aware_utc(value):
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _maybe_timeout_stale_open_turn(db: Session, *, conversation: WebchatConversation, turn: WebchatAITurn) -> bool:
    timeout_seconds = _timeout_seconds_for_status(turn)
    anchor = _turn_anchor_time(turn)
    if timeout_seconds is None or anchor is None:
        return False
    anchor = _ensure_aware_utc(anchor)
    now = _ensure_aware_utc(utc_now())
    if anchor is None or now is None:
        return False
    if anchor + timedelta(seconds=timeout_seconds) > now:
        return False
    reason = f"ai_turn_watchdog_timeout:{turn.status}:{timeout_seconds}s"
    mark_ai_turn_timeout(db, conversation=conversation, turn=turn, reason=reason)
    safe_write_webchat_event(
        db,
        conversation_id=conversation.id,
        ticket_id=turn.ticket_id,
        event_type="webchat_ai_snapshot_reconciled",
        payload={"ai_turn_id": turn.id, "action": "timeout", "reason": reason},
    )
    return True


def reconcile_webchat_ai_state(db: Session, conversation_id: int | None = None) -> dict[str, int]:
    """Repair stale WebChat AI turn snapshots.

    WebchatAITurn is the durable source of truth. The conversation active_ai_*
    fields are a fast-read cache for public polling and must never keep typing
    dots alive after a terminal, dead, or timed-out turn.
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
    timed_out = 0
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
            safe_write_webchat_event(
                db,
                conversation_id=conversation.id,
                ticket_id=conversation.ticket_id,
                event_type="webchat_ai_snapshot_reconciled",
                payload={"action": "clear_missing_turn"},
            )
            cleared += 1
            continue

        if turn.status in AI_TURN_TERMINAL_STATUSES:
            clear_active_ai_snapshot_if_current(db, conversation=conversation, turn=turn)
            safe_write_webchat_event(
                db,
                conversation_id=conversation.id,
                ticket_id=turn.ticket_id,
                event_type="webchat_ai_snapshot_reconciled",
                payload={"ai_turn_id": turn.id, "action": "clear_terminal", "status": turn.status},
            )
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
                safe_write_webchat_event(
                    db,
                    conversation_id=conversation.id,
                    ticket_id=turn.ticket_id,
                    event_type="ai_turn.failed",
                    payload={"ai_turn_id": turn.id, "reason": turn.status_reason, "job_id": job.id},
                )
                safe_write_webchat_event(
                    db,
                    conversation_id=conversation.id,
                    ticket_id=turn.ticket_id,
                    event_type="webchat_ai_snapshot_reconciled",
                    payload={"ai_turn_id": turn.id, "action": "clear_dead_job", "job_id": job.id},
                )
                failed += 1
                continue

        if _maybe_timeout_stale_open_turn(db, conversation=conversation, turn=turn):
            timed_out += 1
            continue

        if conversation.active_ai_status != turn.status:
            conversation.active_ai_status = turn.status
            conversation.active_ai_for_message_id = turn.latest_visitor_message_id or turn.trigger_message_id
            conversation.active_ai_context_cutoff_message_id = turn.context_cutoff_message_id
            conversation.active_ai_updated_at = utc_now()
            safe_write_webchat_event(
                db,
                conversation_id=conversation.id,
                ticket_id=turn.ticket_id,
                event_type="webchat_ai_snapshot_reconciled",
                payload={"ai_turn_id": turn.id, "action": "sync_status", "status": turn.status},
            )
            promoted += 1

    db.flush()
    return {"inspected": inspected, "cleared": cleared, "failed": failed, "promoted": promoted, "timed_out": timed_out}
