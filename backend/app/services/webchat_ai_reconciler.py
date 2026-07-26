from __future__ import annotations

import json
from datetime import timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..enums import JobStatus
from ..models import BackgroundJob
from ..settings import get_settings
from ..utils.time import utc_now
from ..webchat_models import WebchatAITurn, WebchatConversation, WebchatMessage
from . import background_jobs
from .background_job_transaction_boundary import _finalize_dead_webchat_ai_job
from .observability import record_webchat_ai_timeout
from .webchat_ai_turn_service import (
    AI_TURN_TERMINAL_STATUSES,
    DEFAULT_BRIDGE_GRACE_SECONDS,
    DEFAULT_FALLBACK_TIMEOUT_SECONDS,
    DEFAULT_PROCESSING_TIMEOUT_SECONDS,
    DEFAULT_QUEUED_TIMEOUT_SECONDS,
    clear_active_ai_snapshot_if_current,
    safe_write_webchat_event,
)

settings = get_settings()


def _status_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _timeout_seconds_for_status(turn: WebchatAITurn) -> int | None:
    status = str(turn.status or "")
    if status == "queued":
        return int(
            getattr(
                settings,
                "webchat_ai_queued_timeout_seconds",
                DEFAULT_QUEUED_TIMEOUT_SECONDS,
            )
            or DEFAULT_QUEUED_TIMEOUT_SECONDS
        )
    if status == "processing":
        return int(
            getattr(
                settings,
                "webchat_ai_processing_timeout_seconds",
                DEFAULT_PROCESSING_TIMEOUT_SECONDS,
            )
            or DEFAULT_PROCESSING_TIMEOUT_SECONDS
        )
    if status == "fallback_generating":
        return int(
            getattr(
                settings,
                "webchat_ai_fallback_timeout_seconds",
                DEFAULT_FALLBACK_TIMEOUT_SECONDS,
            )
            or DEFAULT_FALLBACK_TIMEOUT_SECONDS
        )
    if status == "bridge_calling":
        bridge_timeout = 20
        grace = int(
            getattr(
                settings,
                "webchat_ai_bridge_timeout_grace_seconds",
                DEFAULT_BRIDGE_GRACE_SECONDS,
            )
            or DEFAULT_BRIDGE_GRACE_SECONDS
        )
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


def _public_outcome_exists(db: Session, *, turn: WebchatAITurn) -> bool:
    if turn.reply_message_id is not None:
        return True
    return bool(
        db.query(WebchatMessage.id)
        .filter(
            WebchatMessage.conversation_id == turn.conversation_id,
            WebchatMessage.ai_turn_id == turn.id,
            WebchatMessage.direction == "agent",
        )
        .first()
    )


def _terminal_job_for_turn(
    db: Session,
    *,
    turn: WebchatAITurn,
    reason: str,
) -> BackgroundJob:
    job = db.get(BackgroundJob, turn.job_id) if turn.job_id is not None else None
    now = utc_now()
    if job is None:
        job = BackgroundJob(
            queue_name="webchat_ai_reply",
            job_type=background_jobs.WEBCHAT_AI_REPLY_JOB,
            payload_json=json.dumps(
                {
                    "conversation_id": turn.conversation_id,
                    "ticket_id": turn.ticket_id,
                    "visitor_message_id": (
                        turn.latest_visitor_message_id or turn.trigger_message_id
                    ),
                    "ai_turn_id": turn.id,
                    "terminal_recovery": True,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            dedupe_key=f"webchat-ai-terminal-recovery:{turn.id}",
            status=JobStatus.dead,
            attempt_count=1,
            max_attempts=1,
            last_error=reason[:500],
            locked_at=None,
            locked_by=None,
            next_run_at=None,
            created_at=now,
            updated_at=now,
        )
        db.add(job)
        db.flush()
        turn.job_id = job.id
    else:
        job.status = JobStatus.dead
        job.attempt_count = max(
            int(job.attempt_count or 0),
            int(job.max_attempts or 1),
        )
        job.last_error = reason[:500]
        job.locked_at = None
        job.locked_by = None
        job.next_run_at = None
        job.updated_at = now
    turn.status_reason = reason[:500]
    turn.updated_at = now
    db.flush()
    return job


def _finalize_terminal_outcome(
    db: Session,
    *,
    conversation: WebchatConversation,
    turn: WebchatAITurn,
    reason: str,
    action: str,
) -> None:
    job = _terminal_job_for_turn(db, turn=turn, reason=reason)
    _finalize_dead_webchat_ai_job(db, job)

    # A public fallback is the customer outcome, not a rewrite of the failed
    # execution cause. Watchdog-expired turns remain ``timeout`` so runtime SLOs,
    # incident triage and historical contracts do not count them as successful
    # model completions. The reply_message_id proves that the customer was not
    # left without a terminal outcome.
    if action == "timeout_terminal_outcome":
        turn.status = "timeout"
        turn.status_reason = reason[:500]
        turn.updated_at = utc_now()
        db.flush()
        safe_write_webchat_event(
            db,
            conversation_id=conversation.id,
            ticket_id=turn.ticket_id,
            event_type="ai_turn.timeout",
            payload={
                "ai_turn_id": turn.id,
                "job_id": job.id,
                "reason_code": "watchdog_timeout",
                "terminal_status": "timeout",
                "customer_outcome_committed": _public_outcome_exists(db, turn=turn),
            },
        )

    safe_write_webchat_event(
        db,
        conversation_id=conversation.id,
        ticket_id=turn.ticket_id,
        event_type="webchat_ai_snapshot_reconciled",
        payload={
            "ai_turn_id": turn.id,
            "action": action,
            "reason": reason,
            "job_id": job.id,
            "terminal_status": turn.status,
            "customer_outcome_committed": _public_outcome_exists(db, turn=turn),
        },
    )


def _maybe_timeout_stale_open_turn(
    db: Session,
    *,
    conversation: WebchatConversation,
    turn: WebchatAITurn,
) -> bool:
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
    record_webchat_ai_timeout(reason)
    _finalize_terminal_outcome(
        db,
        conversation=conversation,
        turn=turn,
        reason=reason,
        action="timeout_terminal_outcome",
    )
    return True


def reconcile_webchat_ai_state(
    db: Session,
    conversation_id: int | None = None,
) -> dict[str, int]:
    """Repair WebChat AI state without allowing silent customer terminal outcomes.

    WebchatAITurn is the durable source of truth. Conversation active_ai_* fields
    are read caches only. Failed, timed-out, or dead-job turns are finalized via
    the one canonical dead-job customer-outcome authority before the cache is
    cleared. Superseded and handoff-cancelled turns remain intentionally silent.
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
    recovered = 0
    for conversation in query.all():
        inspected += 1
        turn = None
        if conversation.active_ai_turn_id:
            turn = (
                db.query(WebchatAITurn)
                .filter(WebchatAITurn.id == conversation.active_ai_turn_id)
                .first()
            )
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
            needs_outcome_repair = (
                turn.status in {"failed", "timeout"}
                and turn.is_public_reply_allowed
                and not _public_outcome_exists(db, turn=turn)
            )
            if needs_outcome_repair:
                _finalize_terminal_outcome(
                    db,
                    conversation=conversation,
                    turn=turn,
                    reason=turn.status_reason
                    or f"terminal_turn_without_outcome:{turn.status}",
                    action="repair_terminal_without_outcome",
                )
                recovered += 1
            else:
                clear_active_ai_snapshot_if_current(
                    db,
                    conversation=conversation,
                    turn=turn,
                )
                safe_write_webchat_event(
                    db,
                    conversation_id=conversation.id,
                    ticket_id=turn.ticket_id,
                    event_type="webchat_ai_snapshot_reconciled",
                    payload={
                        "ai_turn_id": turn.id,
                        "action": "clear_terminal",
                        "status": turn.status,
                    },
                )
                cleared += 1
            continue

        if turn.job_id:
            job = db.get(BackgroundJob, turn.job_id)
            if job is not None and _status_value(job.status) == _status_value(
                JobStatus.dead
            ):
                _finalize_dead_webchat_ai_job(db, job)
                safe_write_webchat_event(
                    db,
                    conversation_id=conversation.id,
                    ticket_id=turn.ticket_id,
                    event_type="webchat_ai_snapshot_reconciled",
                    payload={
                        "ai_turn_id": turn.id,
                        "action": "finalize_dead_job_outcome",
                        "job_id": job.id,
                        "status": turn.status,
                    },
                )
                failed += 1
                recovered += 1
                continue

        if _maybe_timeout_stale_open_turn(
            db,
            conversation=conversation,
            turn=turn,
        ):
            timed_out += 1
            recovered += 1
            continue

        if conversation.active_ai_status != turn.status:
            conversation.active_ai_status = turn.status
            conversation.active_ai_for_message_id = (
                turn.latest_visitor_message_id or turn.trigger_message_id
            )
            conversation.active_ai_context_cutoff_message_id = (
                turn.context_cutoff_message_id
            )
            conversation.active_ai_updated_at = utc_now()
            safe_write_webchat_event(
                db,
                conversation_id=conversation.id,
                ticket_id=turn.ticket_id,
                event_type="webchat_ai_snapshot_reconciled",
                payload={
                    "ai_turn_id": turn.id,
                    "action": "sync_status",
                    "status": turn.status,
                },
            )
            promoted += 1

    db.flush()
    return {
        "inspected": inspected,
        "cleared": cleared,
        "failed": failed,
        "promoted": promoted,
        "timed_out": timed_out,
        "recovered": recovered,
    }
