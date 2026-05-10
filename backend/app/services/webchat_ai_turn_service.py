from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any, Callable

from sqlalchemy.orm import Session

from ..models import BackgroundJob
from ..utils.time import utc_now
from ..webchat_models import WebchatAITurn, WebchatConversation, WebchatEvent, WebchatMessage
from .observability import record_webchat_ai_stale_suppressed, record_webchat_ai_timeout, record_webchat_ai_turn_metric

LOGGER = logging.getLogger("nexusdesk")

AI_TURN_OPEN_STATUSES = {"queued", "processing", "bridge_calling", "fallback_generating"}
AI_TURN_TERMINAL_STATUSES = {"completed", "superseded", "failed", "timeout", "cancelled"}
AI_TURN_TYPING_STATUSES = {"queued", "processing", "bridge_calling", "fallback_generating"}
DEFAULT_AI_TURN_DEBOUNCE_SECONDS = 1
DEFAULT_QUEUED_TIMEOUT_SECONDS = 120
DEFAULT_PROCESSING_TIMEOUT_SECONDS = 90
DEFAULT_BRIDGE_GRACE_SECONDS = 15
DEFAULT_FALLBACK_TIMEOUT_SECONDS = 60

JobFactory = Callable[[dict[str, Any], str, Any], BackgroundJob]


def _status_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _event_payload(**payload: Any) -> str:
    return json.dumps({key: value for key, value in payload.items() if value is not None}, ensure_ascii=False)


def _turn_duration_ms(turn: WebchatAITurn) -> int | None:
    try:
        end = turn.completed_at or turn.updated_at or utc_now()
        start = turn.created_at or turn.started_at
        if not end or not start:
            return None
        return max(0, int((end - start).total_seconds() * 1000))
    except Exception:
        return None


def _sanitized_event_failure(exc: Exception) -> dict[str, Any]:
    return {"error_type": type(exc).__name__}


def write_webchat_event(
    db: Session,
    *,
    conversation_id: int,
    ticket_id: int,
    event_type: str,
    payload: dict[str, Any] | None = None,
    best_effort: bool = False,
) -> WebchatEvent | None:
    def _insert() -> WebchatEvent:
        row = WebchatEvent(
            conversation_id=conversation_id,
            ticket_id=ticket_id,
            event_type=event_type,
            payload_json=json.dumps(payload or {}, ensure_ascii=False),
            created_at=utc_now(),
        )
        db.add(row)
        db.flush()
        return row

    if not best_effort:
        return _insert()

    try:
        with db.begin_nested():
            return _insert()
    except Exception as exc:  # pragma: no cover - exercised through monkeypatch failure tests
        LOGGER.warning(
            "webchat_event_write_failed_best_effort",
            extra={
                "event_payload": {
                    "conversation_id": conversation_id,
                    "ticket_id": ticket_id,
                    "event_type": event_type,
                    **_sanitized_event_failure(exc),
                }
            },
        )
        return None


def safe_write_webchat_event(
    db: Session,
    *,
    conversation_id: int,
    ticket_id: int,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> WebchatEvent | None:
    return write_webchat_event(
        db,
        conversation_id=conversation_id,
        ticket_id=ticket_id,
        event_type=event_type,
        payload=payload,
        best_effort=True,
    )


def ai_snapshot(conversation: WebchatConversation) -> dict[str, Any]:
    status = getattr(conversation, "active_ai_status", None)
    turn_id = getattr(conversation, "active_ai_turn_id", None)
    return {
        "ai_pending": bool(status in AI_TURN_TYPING_STATUSES and turn_id),
        "ai_status": status,
        "ai_turn_id": turn_id,
        "ai_pending_for_message_id": getattr(conversation, "active_ai_for_message_id", None),
    }


def _clear_active_snapshot(conversation: WebchatConversation) -> None:
    conversation.active_ai_turn_id = None
    conversation.active_ai_status = None
    conversation.active_ai_for_message_id = None
    conversation.active_ai_context_cutoff_message_id = None
    conversation.active_ai_started_at = None
    conversation.active_ai_updated_at = utc_now()


def clear_active_ai_snapshot_if_current(db: Session, *, conversation: WebchatConversation, turn: WebchatAITurn) -> bool:
    if getattr(conversation, "active_ai_turn_id", None) != turn.id:
        return False
    _clear_active_snapshot(conversation)
    db.flush()
    return True


def _activate_turn(db: Session, *, conversation: WebchatConversation, turn: WebchatAITurn) -> None:
    now = utc_now()
    conversation.active_ai_turn_id = turn.id
    conversation.active_ai_status = turn.status
    conversation.active_ai_for_message_id = turn.latest_visitor_message_id or turn.trigger_message_id
    conversation.active_ai_context_cutoff_message_id = turn.context_cutoff_message_id
    conversation.active_ai_started_at = turn.started_at or now
    conversation.active_ai_updated_at = now
    conversation.updated_at = now
    db.flush()


def _promote_next_turn(db: Session, *, conversation: WebchatConversation) -> WebchatAITurn | None:
    next_turn_id = getattr(conversation, "next_ai_turn_id", None)
    if not next_turn_id:
        return None
    next_turn = db.query(WebchatAITurn).filter(WebchatAITurn.id == next_turn_id).first()
    conversation.next_ai_turn_id = None
    if not next_turn or next_turn.status not in AI_TURN_OPEN_STATUSES:
        _clear_active_snapshot(conversation)
        db.flush()
        return None
    _activate_turn(db, conversation=conversation, turn=next_turn)
    safe_write_webchat_event(
        db,
        conversation_id=conversation.id,
        ticket_id=conversation.ticket_id,
        event_type="ai_turn.queued",
        payload={"ai_turn_id": next_turn.id, "promoted_from_next": True},
    )
    record_webchat_ai_turn_metric("queued")
    return next_turn


def schedule_webchat_ai_turn(
    db: Session,
    *,
    conversation: WebchatConversation,
    ticket_id: int,
    visitor_message: WebchatMessage,
    create_job: JobFactory,
    debounce_seconds: int = DEFAULT_AI_TURN_DEBOUNCE_SECONDS,
) -> dict[str, Any]:
    now = utc_now()
    debounce_at = now + timedelta(seconds=max(0, int(debounce_seconds or 0)))
    safe_write_webchat_event(
        db,
        conversation_id=conversation.id,
        ticket_id=ticket_id,
        event_type="message.created",
        payload={"message_id": visitor_message.id, "direction": visitor_message.direction},
    )

    active_turn = None
    if getattr(conversation, "active_ai_turn_id", None):
        active_turn = db.query(WebchatAITurn).filter(WebchatAITurn.id == conversation.active_ai_turn_id).first()
        if active_turn and active_turn.status in AI_TURN_TERMINAL_STATUSES:
            clear_active_ai_snapshot_if_current(db, conversation=conversation, turn=active_turn)
            active_turn = None

    if active_turn and active_turn.status == "queued":
        active_turn.latest_visitor_message_id = visitor_message.id
        active_turn.updated_at = now
        conversation.active_ai_for_message_id = visitor_message.id
        conversation.active_ai_updated_at = now
        if active_turn.job_id:
            job = db.query(BackgroundJob).filter(BackgroundJob.id == active_turn.job_id).first()
            if job is not None:
                job.next_run_at = debounce_at
                job.updated_at = now
        safe_write_webchat_event(
            db,
            conversation_id=conversation.id,
            ticket_id=ticket_id,
            event_type="ai_turn.coalesced",
            payload={"ai_turn_id": active_turn.id, "latest_visitor_message_id": visitor_message.id},
        )
        record_webchat_ai_turn_metric("coalesced")
        db.flush()
        return {**ai_snapshot(conversation), "coalesced": True}

    if active_turn and active_turn.status in {"processing", "bridge_calling", "fallback_generating"}:
        next_turn = WebchatAITurn(
            conversation_id=conversation.id,
            ticket_id=ticket_id,
            trigger_message_id=visitor_message.id,
            latest_visitor_message_id=visitor_message.id,
            status="queued",
            is_public_reply_allowed=True,
            created_at=now,
            updated_at=now,
        )
        db.add(next_turn)
        db.flush()
        job = create_job(
            {
                "conversation_id": conversation.id,
                "ticket_id": ticket_id,
                "visitor_message_id": visitor_message.id,
                "ai_turn_id": next_turn.id,
            },
            f"webchat-ai-turn:{next_turn.id}",
            debounce_at,
        )
        next_turn.job_id = job.id
        conversation.next_ai_turn_id = next_turn.id
        conversation.active_ai_updated_at = now
        safe_write_webchat_event(
            db,
            conversation_id=conversation.id,
            ticket_id=ticket_id,
            event_type="ai_turn.queued",
            payload={"ai_turn_id": next_turn.id, "queued_as_next": True, "active_ai_turn_id": active_turn.id},
        )
        record_webchat_ai_turn_metric("queued")
        db.flush()
        return {**ai_snapshot(conversation), "coalesced": False, "next_ai_turn_id": next_turn.id}

    turn = WebchatAITurn(
        conversation_id=conversation.id,
        ticket_id=ticket_id,
        trigger_message_id=visitor_message.id,
        latest_visitor_message_id=visitor_message.id,
        status="queued",
        is_public_reply_allowed=True,
        created_at=now,
        updated_at=now,
    )
    db.add(turn)
    db.flush()
    job = create_job(
        {
            "conversation_id": conversation.id,
            "ticket_id": ticket_id,
            "visitor_message_id": visitor_message.id,
            "ai_turn_id": turn.id,
        },
        f"webchat-ai-turn:{turn.id}",
        debounce_at,
    )
    turn.job_id = job.id
    _activate_turn(db, conversation=conversation, turn=turn)
    safe_write_webchat_event(
        db,
        conversation_id=conversation.id,
        ticket_id=ticket_id,
        event_type="ai_turn.queued",
        payload={"ai_turn_id": turn.id, "trigger_message_id": visitor_message.id},
    )
    record_webchat_ai_turn_metric("queued")
    db.flush()
    return {**ai_snapshot(conversation), "coalesced": False}


def mark_ai_turn_processing(db: Session, *, conversation: WebchatConversation, turn: WebchatAITurn) -> None:
    now = utc_now()
    turn.status = "processing"
    turn.started_at = turn.started_at or now
    turn.updated_at = now
    if conversation.active_ai_turn_id == turn.id:
        conversation.active_ai_status = "processing"
        conversation.active_ai_updated_at = now
    safe_write_webchat_event(db, conversation_id=conversation.id, ticket_id=turn.ticket_id, event_type="ai_turn.processing", payload={"ai_turn_id": turn.id})
    record_webchat_ai_turn_metric("processing")
    db.flush()


def mark_ai_turn_bridge_calling(db: Session, *, conversation: WebchatConversation, turn: WebchatAITurn, context_cutoff_message_id: int | None) -> None:
    now = utc_now()
    turn.status = "bridge_calling"
    turn.context_cutoff_message_id = context_cutoff_message_id
    turn.updated_at = now
    if conversation.active_ai_turn_id == turn.id:
        conversation.active_ai_status = "bridge_calling"
        conversation.active_ai_context_cutoff_message_id = context_cutoff_message_id
        conversation.active_ai_updated_at = now
    safe_write_webchat_event(db, conversation_id=conversation.id, ticket_id=turn.ticket_id, event_type="ai_turn.bridge_calling", payload={"ai_turn_id": turn.id, "context_cutoff_message_id": context_cutoff_message_id})
    record_webchat_ai_turn_metric("bridge_calling")
    db.flush()


def mark_ai_turn_failed(db: Session, *, conversation: WebchatConversation, turn: WebchatAITurn, reason: str) -> None:
    now = utc_now()
    turn.status = "failed"
    turn.status_reason = reason
    turn.completed_at = now
    turn.updated_at = now
    safe_write_webchat_event(db, conversation_id=conversation.id, ticket_id=turn.ticket_id, event_type="ai_turn.failed", payload={"ai_turn_id": turn.id, "reason": reason})
    record_webchat_ai_turn_metric("failed", _turn_duration_ms(turn))
    clear_active_ai_snapshot_if_current(db, conversation=conversation, turn=turn)
    db.flush()


def mark_ai_turn_timeout(db: Session, *, conversation: WebchatConversation, turn: WebchatAITurn, reason: str) -> None:
    now = utc_now()
    turn.status = "timeout"
    turn.status_reason = reason
    turn.completed_at = now
    turn.updated_at = now
    safe_write_webchat_event(db, conversation_id=conversation.id, ticket_id=turn.ticket_id, event_type="ai_turn.timeout", payload={"ai_turn_id": turn.id, "reason": reason})
    record_webchat_ai_timeout(reason)
    record_webchat_ai_turn_metric("timeout", _turn_duration_ms(turn))
    clear_active_ai_snapshot_if_current(db, conversation=conversation, turn=turn)
    db.flush()


def should_suppress_stale_reply(db: Session, *, conversation: WebchatConversation, turn: WebchatAITurn | None) -> bool:
    if turn is None:
        return False
    if not turn.is_public_reply_allowed or turn.status in AI_TURN_TERMINAL_STATUSES:
        return True
    cutoff_id = turn.context_cutoff_message_id or turn.latest_visitor_message_id or turn.trigger_message_id
    current_latest = latest_visitor_message_id(db, conversation_id=conversation.id)
    return bool(cutoff_id is not None and current_latest is not None and current_latest > cutoff_id)


def suppress_stale_reply_if_needed(db: Session, *, conversation: WebchatConversation, turn: WebchatAITurn | None, reason: str = "newer_message_before_reply") -> bool:
    if turn is None or not should_suppress_stale_reply(db, conversation=conversation, turn=turn):
        return False
    supersede_ai_turn(db, conversation=conversation, turn=turn, reason=reason)
    safe_write_webchat_event(
        db,
        conversation_id=conversation.id,
        ticket_id=turn.ticket_id,
        event_type="webchat_ai_reply_suppressed_stale",
        payload={"ai_turn_id": turn.id, "reason": reason, "latest_visitor_message_id": latest_visitor_message_id(db, conversation_id=conversation.id)},
    )
    record_webchat_ai_stale_suppressed(reason)
    return True


def complete_ai_turn_with_reply(db: Session, *, conversation: WebchatConversation, turn: WebchatAITurn, result: dict[str, Any]) -> None:
    if result.get("status") == "superseded":
        supersede_ai_turn(db, conversation=conversation, turn=turn, reason=result.get("reason") or "reply_suppressed")
        return
    now = utc_now()
    turn.status = "completed"
    turn.reply_message_id = result.get("message_id")
    turn.reply_source = result.get("reply_source")
    turn.fallback_reason = result.get("fallback_reason")
    turn.fact_gate_reason = result.get("fact_gate_reason")
    turn.bridge_elapsed_ms = result.get("bridge_elapsed_ms")
    wait_timeout_ms = result.get("bridge_wait_timeout_ms")
    if wait_timeout_ms is not None:
        turn.bridge_timeout_ms = int(wait_timeout_ms)
    turn.completed_at = now
    turn.updated_at = now
    safe_write_webchat_event(db, conversation_id=conversation.id, ticket_id=turn.ticket_id, event_type="message.created", payload={"message_id": turn.reply_message_id, "direction": "agent", "ai_turn_id": turn.id})
    safe_write_webchat_event(db, conversation_id=conversation.id, ticket_id=turn.ticket_id, event_type="ai_turn.completed" if not turn.fallback_reason else "ai_turn.fallback", payload={"ai_turn_id": turn.id, "message_id": turn.reply_message_id, "reply_source": turn.reply_source, "fallback_reason": turn.fallback_reason})
    record_webchat_ai_turn_metric("fallback" if turn.fallback_reason else "completed", _turn_duration_ms(turn))
    if conversation.active_ai_turn_id == turn.id:
        if conversation.next_ai_turn_id:
            _promote_next_turn(db, conversation=conversation)
        else:
            _clear_active_snapshot(conversation)
    db.flush()


def supersede_ai_turn(db: Session, *, conversation: WebchatConversation, turn: WebchatAITurn, superseded_by_turn_id: int | None = None, reason: str | None = None) -> None:
    now = utc_now()
    turn.status = "superseded"
    turn.status_reason = reason or "newer_visitor_message_present"
    turn.superseded_by_turn_id = superseded_by_turn_id
    turn.is_public_reply_allowed = False
    turn.completed_at = now
    turn.updated_at = now
    safe_write_webchat_event(db, conversation_id=conversation.id, ticket_id=turn.ticket_id, event_type="ai_turn.superseded", payload={"ai_turn_id": turn.id, "superseded_by_turn_id": superseded_by_turn_id, "reason": turn.status_reason})
    record_webchat_ai_turn_metric("superseded", _turn_duration_ms(turn))
    if conversation.active_ai_turn_id == turn.id:
        if conversation.next_ai_turn_id:
            _promote_next_turn(db, conversation=conversation)
        else:
            _clear_active_snapshot(conversation)
    db.flush()


def latest_visitor_message_id(db: Session, *, conversation_id: int) -> int | None:
    row = (
        db.query(WebchatMessage.id)
        .filter(WebchatMessage.conversation_id == conversation_id, WebchatMessage.direction == "visitor")
        .order_by(WebchatMessage.id.desc())
        .first()
    )
    return int(row[0]) if row else None


def process_webchat_ai_turn_job(db: Session, *, ai_turn_id: int, legacy_process: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    turn = db.query(WebchatAITurn).filter(WebchatAITurn.id == ai_turn_id).first()
    if not turn:
        return {"status": "skipped", "reason": "ai_turn_not_found"}
    conversation = db.query(WebchatConversation).filter(WebchatConversation.id == turn.conversation_id).first()
    if not conversation:
        turn.status = "failed"
        turn.status_reason = "conversation_not_found"
        turn.completed_at = utc_now()
        record_webchat_ai_turn_metric("failed", _turn_duration_ms(turn))
        return {"status": "failed", "reason": "conversation_not_found"}
    if turn.status != "queued":
        return {"status": "skipped", "reason": f"ai_turn_not_queued:{turn.status}"}

    mark_ai_turn_processing(db, conversation=conversation, turn=turn)
    cutoff_id = latest_visitor_message_id(db, conversation_id=conversation.id)
    mark_ai_turn_bridge_calling(db, conversation=conversation, turn=turn, context_cutoff_message_id=cutoff_id)

    if suppress_stale_reply_if_needed(db, conversation=conversation, turn=turn, reason="stale_before_ai_call"):
        return {"status": "superseded", "reason": "stale_before_ai_call"}

    try:
        result = legacy_process(db, conversation_id=turn.conversation_id, ticket_id=turn.ticket_id, visitor_message_id=turn.latest_visitor_message_id or turn.trigger_message_id)
    except Exception as exc:
        mark_ai_turn_failed(db, conversation=conversation, turn=turn, reason=f"{type(exc).__name__}: {exc}"[:500])
        raise
    complete_ai_turn_with_reply(db, conversation=conversation, turn=turn, result=result or {})
    return {"status": "done", "ai_turn_id": turn.id, **(result or {})}
