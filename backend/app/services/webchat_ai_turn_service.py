from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, Callable

from sqlalchemy.orm import Session

from ..models import BackgroundJob
from ..utils.time import utc_now
from ..webchat_models import WebchatAITurn, WebchatConversation, WebchatEvent, WebchatMessage

AI_TURN_OPEN_STATUSES = {"queued", "processing", "bridge_calling", "fallback_generating"}
AI_TURN_TERMINAL_STATUSES = {"completed", "superseded", "failed", "timeout", "cancelled"}
AI_TURN_TYPING_STATUSES = {"queued", "processing", "bridge_calling", "fallback_generating"}
DEFAULT_AI_TURN_DEBOUNCE_SECONDS = 1

JobFactory = Callable[[dict[str, Any], str, Any], BackgroundJob]


def _status_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _event_payload(**payload: Any) -> str:
    return json.dumps({key: value for key, value in payload.items() if value is not None}, ensure_ascii=False)


def write_webchat_event(
    db: Session,
    *,
    conversation_id: int,
    ticket_id: int,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> WebchatEvent:
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
    write_webchat_event(
        db,
        conversation_id=conversation.id,
        ticket_id=conversation.ticket_id,
        event_type="ai_turn.queued",
        payload={"ai_turn_id": next_turn.id, "promoted_from_next": True},
    )
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
    write_webchat_event(
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
        write_webchat_event(
            db,
            conversation_id=conversation.id,
            ticket_id=ticket_id,
            event_type="ai_turn.coalesced",
            payload={"ai_turn_id": active_turn.id, "latest_visitor_message_id": visitor_message.id},
        )
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
        write_webchat_event(
            db,
            conversation_id=conversation.id,
            ticket_id=ticket_id,
            event_type="ai_turn.queued",
            payload={"ai_turn_id": next_turn.id, "queued_as_next": True, "active_ai_turn_id": active_turn.id},
        )
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
    write_webchat_event(
        db,
        conversation_id=conversation.id,
        ticket_id=ticket_id,
        event_type="ai_turn.queued",
        payload={"ai_turn_id": turn.id, "trigger_message_id": visitor_message.id},
    )
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
    write_webchat_event(db, conversation_id=conversation.id, ticket_id=turn.ticket_id, event_type="ai_turn.processing", payload={"ai_turn_id": turn.id})
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
    write_webchat_event(db, conversation_id=conversation.id, ticket_id=turn.ticket_id, event_type="ai_turn.bridge_calling", payload={"ai_turn_id": turn.id, "context_cutoff_message_id": context_cutoff_message_id})
    db.flush()


def complete_ai_turn_with_reply(db: Session, *, conversation: WebchatConversation, turn: WebchatAITurn, result: dict[str, Any]) -> None:
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
    write_webchat_event(db, conversation_id=conversation.id, ticket_id=turn.ticket_id, event_type="message.created", payload={"message_id": turn.reply_message_id, "direction": "agent", "ai_turn_id": turn.id})
    write_webchat_event(db, conversation_id=conversation.id, ticket_id=turn.ticket_id, event_type="ai_turn.completed" if not turn.fallback_reason else "ai_turn.fallback", payload={"ai_turn_id": turn.id, "message_id": turn.reply_message_id, "reply_source": turn.reply_source, "fallback_reason": turn.fallback_reason})
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
    write_webchat_event(db, conversation_id=conversation.id, ticket_id=turn.ticket_id, event_type="ai_turn.superseded", payload={"ai_turn_id": turn.id, "superseded_by_turn_id": superseded_by_turn_id, "reason": turn.status_reason})
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
        return {"status": "failed", "reason": "conversation_not_found"}
    if turn.status != "queued":
        return {"status": "skipped", "reason": f"ai_turn_not_queued:{turn.status}"}

    mark_ai_turn_processing(db, conversation=conversation, turn=turn)
    cutoff_id = latest_visitor_message_id(db, conversation_id=conversation.id)
    mark_ai_turn_bridge_calling(db, conversation=conversation, turn=turn, context_cutoff_message_id=cutoff_id)

    # If this turn was already stale before the model call, suppress it and promote the queued next turn.
    current_latest = latest_visitor_message_id(db, conversation_id=conversation.id)
    if cutoff_id is not None and current_latest is not None and current_latest > cutoff_id:
        supersede_ai_turn(db, conversation=conversation, turn=turn)
        return {"status": "superseded", "reason": "stale_before_ai_call"}

    result = legacy_process(db, conversation_id=turn.conversation_id, ticket_id=turn.ticket_id, visitor_message_id=turn.latest_visitor_message_id or turn.trigger_message_id)
    complete_ai_turn_with_reply(db, conversation=conversation, turn=turn, result=result or {})
    return {"status": "done", "ai_turn_id": turn.id, **(result or {})}
