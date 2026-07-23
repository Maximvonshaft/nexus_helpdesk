from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Iterable

from sqlalchemy import text, update

LOGGER = logging.getLogger(__name__)


def _exception_reason(exc: Exception) -> str:
    return f"Unhandled background job exception: {type(exc).__name__}"


def _is_sqlalchemy_session(db: Any) -> bool:
    return hasattr(db, "execute") and getattr(db, "bind", None) is not None


def _claim_token(worker_id: str | None) -> str:
    prefix = (worker_id or "job-worker").strip() or "job-worker"
    return f"{prefix[:80]}:{uuid.uuid4().hex}"


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


def commit_webchat_agent_provider_boundary(db: Any) -> None:
    """Persist bridge state and release database locks before Provider I/O.

    WebChat Agent generation is an external call that may overlap an operator
    takeover. Keeping the bridge-state transaction open would hold Conversation
    and Agent-turn locks for the entire Provider latency window. This explicit
    attempt boundary makes the in-flight state durable, releases those locks,
    and lets the post-Provider phase re-read committed human ownership before a
    public reply is persisted.
    """

    if not _is_sqlalchemy_session(db):
        return
    db.commit()


def _owns_job_lease(db: Any, *, job_id: int, lease_token: str) -> bool:
    if not _is_sqlalchemy_session(db):
        return True
    from . import background_jobs

    bind = db.get_bind() if hasattr(db, "get_bind") else getattr(db, "bind", None)
    if bind is None:
        return False
    engine = getattr(bind, "engine", bind)
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


def _status_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _load_job_payload(job: Any) -> dict[str, Any]:
    try:
        value = json.loads(job.payload_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _lock_one(query: Any, db: Any):
    if (
        getattr(db, "bind", None) is not None
        and db.bind.dialect.name.startswith("postgresql")
    ):
        query = query.with_for_update()
    return query.first()


def _finalize_dead_webchat_ai_job(db: Any, job: Any) -> None:
    """Commit one customer terminal outcome when the canonical AI job is dead.

    This is part of the background transaction boundary, not a second reply
    service. It reuses the sole fallback wording, AI reply contract and
    customer-visible persistence authorities. A dead job is not allowed to be
    committed unless it has either produced one public outcome or been safely
    suppressed by a newer message or committed human ownership.
    """

    from ..enums import ConversationState, EventType, MessageStatus, SourceChannel, TicketStatus
    from ..models import Ticket
    from ..utils.time import utc_now
    from ..webchat_models import WebchatAITurn, WebchatConversation, WebchatMessage
    from . import background_jobs
    from .agent_runtime.terminal_reply import customer_visible_fallback
    from .ai_reply_contract import build_ai_reply_contract
    from .customer_language import resolve_conversation_language
    from .customer_visible_message_service import create_customer_visible_message
    from .customer_visible_policy import evaluate_customer_visible_policy
    from .sla_service import evaluate_sla, update_first_response
    from .webchat_ai_turn_service import (
        complete_ai_turn_with_reply,
        is_ai_suspended_for_handoff,
        latest_visitor_message_id,
        supersede_ai_turn,
    )

    if job.job_type != background_jobs.WEBCHAT_AI_REPLY_JOB:
        return
    if _status_value(job.status) != background_jobs.JobStatus.dead.value:
        return

    payload = _load_job_payload(job)
    raw_turn_id = payload.get("ai_turn_id")
    turn = None
    if raw_turn_id is not None:
        try:
            turn = _lock_one(
                db.query(WebchatAITurn).filter(WebchatAITurn.id == int(raw_turn_id)),
                db,
            )
        except (TypeError, ValueError):
            turn = None
    if turn is None:
        turn = _lock_one(
            db.query(WebchatAITurn)
            .filter(WebchatAITurn.job_id == job.id)
            .order_by(WebchatAITurn.id.desc()),
            db,
        )
    if turn is None:
        raise RuntimeError("dead_webchat_ai_job_turn_missing")

    conversation = _lock_one(
        db.query(WebchatConversation).filter(
            WebchatConversation.id == turn.conversation_id
        ),
        db,
    )
    visitor_message = db.get(
        WebchatMessage,
        turn.latest_visitor_message_id or turn.trigger_message_id,
    )
    if conversation is None or visitor_message is None:
        raise RuntimeError("dead_webchat_ai_job_context_missing")
    if visitor_message.conversation_id != conversation.id:
        raise RuntimeError("dead_webchat_ai_job_context_mismatch")

    existing = (
        db.query(WebchatMessage)
        .filter(
            WebchatMessage.conversation_id == conversation.id,
            WebchatMessage.ai_turn_id == turn.id,
            WebchatMessage.direction == "agent",
        )
        .order_by(WebchatMessage.id.asc())
        .first()
    )
    if existing is not None:
        complete_ai_turn_with_reply(
            db,
            conversation=conversation,
            turn=turn,
            result={
                "status": "done",
                "message_id": existing.id,
                "reply_source": "agent_runtime:fallback",
                "fallback_reason": "background_job_exhausted",
                "runtime_trace": {
                    "error_code": "background_job_exhausted",
                    "attempt_count": int(job.attempt_count or 0),
                },
            },
        )
        job.last_error = "webchat_ai_attempts_exhausted"
        return

    if not turn.is_public_reply_allowed or is_ai_suspended_for_handoff(conversation):
        if turn.status not in {"completed", "superseded", "cancelled"}:
            supersede_ai_turn(
                db,
                conversation=conversation,
                turn=turn,
                reason="handoff_started_before_terminal_fallback",
            )
        job.last_error = "webchat_ai_terminal_fallback_suppressed_by_handoff"
        return

    latest_id = latest_visitor_message_id(db, conversation_id=conversation.id)
    cutoff_id = (
        turn.context_cutoff_message_id
        or turn.latest_visitor_message_id
        or turn.trigger_message_id
    )
    if latest_id is not None and cutoff_id is not None and latest_id > cutoff_id:
        if turn.status not in {"completed", "superseded", "cancelled"}:
            supersede_ai_turn(
                db,
                conversation=conversation,
                turn=turn,
                reason="newer_message_before_terminal_fallback",
            )
        job.last_error = "webchat_ai_terminal_fallback_suppressed_as_stale"
        return

    later_agent_message = (
        db.query(WebchatMessage.id)
        .filter(
            WebchatMessage.conversation_id == conversation.id,
            WebchatMessage.direction == "agent",
            WebchatMessage.id > visitor_message.id,
        )
        .order_by(WebchatMessage.id.asc())
        .first()
    )
    if later_agent_message is not None:
        if turn.status not in {"completed", "superseded", "cancelled"}:
            supersede_ai_turn(
                db,
                conversation=conversation,
                turn=turn,
                reason="customer_visible_reply_already_committed",
            )
        job.last_error = "webchat_ai_terminal_fallback_suppressed_existing_reply"
        return

    previous_messages = [
        row[0]
        for row in (
            db.query(WebchatMessage.body)
            .filter(
                WebchatMessage.conversation_id == conversation.id,
                WebchatMessage.direction == "visitor",
                WebchatMessage.id < visitor_message.id,
            )
            .order_by(WebchatMessage.id.asc())
            .all()
        )
    ]
    language = resolve_conversation_language(
        visitor_message.body,
        previous_customer_messages=previous_messages,
    ).language
    body = customer_visible_fallback(language, visitor_message.body)
    policy = evaluate_customer_visible_policy(body)
    if not policy.allowed or not policy.normalized_body.strip():
        raise RuntimeError("customer_visible_terminal_fallback_rejected")
    body = policy.normalized_body

    is_whatsapp = str(conversation.channel_key or "").strip().lower() == SourceChannel.whatsapp.value
    channel = SourceChannel.whatsapp if is_whatsapp else SourceChannel.web_chat
    ticket = db.get(Ticket, turn.ticket_id) if turn.ticket_id is not None else None
    if conversation.ticket_id is not None and (
        ticket is None or ticket.id != conversation.ticket_id
    ):
        raise RuntimeError("dead_webchat_ai_job_ticket_mismatch")

    safe_trace = {
        "error_code": "background_job_exhausted",
        "attempt_count": int(job.attempt_count or 0),
    }
    contract = build_ai_reply_contract(
        body=body,
        runtime_trace=safe_trace,
        safety_status="passed",
        reply_type="clarifying_question",
        channel=channel.value,
    )
    provider_status = (
        "whatsapp_ai_terminal_fallback_queued"
        if is_whatsapp
        else "webchat_ai_terminal_fallback_sent"
    )
    visible = create_customer_visible_message(
        db,
        ticket=ticket,
        conversation=conversation,
        channel=channel,
        body=body,
        origin="provider_runtime",
        created_by=None,
        provider_status=provider_status,
        ai_contract=contract,
        outbound_status=None if is_whatsapp else MessageStatus.sent,
        ai_turn_id=turn.id,
        delivery_status="queued" if is_whatsapp else "sent",
        metadata_json={
            "terminal_fallback": True,
            "reason_code": "background_job_exhausted",
            "attempt_count": int(job.attempt_count or 0),
            "language": language,
        },
        author_label="AI Assistant",
        safety_level=policy.level,
        safety_reasons_json=json.dumps(policy.reasons, ensure_ascii=False),
        create_external_comment=ticket is not None,
        event_type=(
            EventType.outbound_queued
            if is_whatsapp
            else EventType.outbound_sent
            if ticket is not None
            else None
        ),
        event_note=(
            "WhatsApp Agent terminal fallback queued"
            if is_whatsapp
            else "Webchat Agent terminal fallback sent"
        ),
        event_payload={
            "conversation_id": conversation.id,
            "ticket_id": ticket.id if ticket else None,
            "visitor_message_id": visitor_message.id,
            "ai_turn_id": turn.id,
            "reply_source": "agent_runtime:fallback",
            "provider_status": provider_status,
            "reason_code": "background_job_exhausted",
        },
    )
    if visible.webchat_message is None:
        raise RuntimeError("customer_visible_terminal_fallback_not_created")

    now = utc_now()
    if ticket is not None:
        ticket.status = TicketStatus.waiting_customer
        ticket.conversation_state = ConversationState.waiting_customer
        ticket.last_ai_update = body
        ticket.last_runtime_reply_at = now
        ticket.updated_at = now
        update_first_response(ticket)
        evaluate_sla(ticket, db)
    conversation.updated_at = now
    conversation.last_seen_at = now
    complete_ai_turn_with_reply(
        db,
        conversation=conversation,
        turn=turn,
        result={
            "status": "done",
            "message_id": visible.webchat_message.id,
            "reply_source": "agent_runtime:fallback",
            "fallback_reason": "background_job_exhausted",
            "runtime_trace": safe_trace,
        },
    )
    job.last_error = "webchat_ai_attempts_exhausted"


def _recover_unhandled_background_job_exception(
    db: Any,
    *,
    job_id: int,
    lease_token: str,
    exc: Exception,
):
    from . import background_jobs

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
    job = (
        db.query(background_jobs.BackgroundJob)
        .filter(background_jobs.BackgroundJob.id == job_id)
        .first()
    )
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
    _finalize_dead_webchat_ai_job(db, job)
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


def _process_claimed_jobs_with_attempt_boundary(
    db: Any,
    jobs: Iterable[Any],
    *,
    lease_token: str,
) -> list[Any]:
    from . import background_jobs

    processed: list[Any] = []
    for job in jobs:
        job_id = job.id
        if not _refresh_job_lease(db, job_id=job_id, lease_token=lease_token):
            continue
        try:
            background_jobs.process_background_job(db, job)
            _finalize_dead_webchat_ai_job(db, job)
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
    """Reuse the background Worker for durable voice and Provider-event work."""

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
    """Dispatch all work owned by the one canonical background Worker."""
    from . import background_jobs

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
    claimed = background_jobs.claim_pending_jobs(
        db,
        limit=limit,
        worker_id=lease_token,
        job_types=[
            background_jobs.SPEEDAF_WORK_ORDER_CREATE_JOB,
            background_jobs.SPEEDAF_ADDRESS_UPDATE_JOB,
            background_jobs.SPEEDAF_VOICE_CALLBACK_JOB,
            background_jobs.EMAIL_MAILBOX_SYNC_JOB,
        ],
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

    lease_token = _claim_token(worker_id)
    claimed = background_jobs.claim_pending_jobs(
        db,
        limit=limit,
        worker_id=lease_token,
        job_types=[background_jobs.WEBCHAT_AI_REPLY_JOB],
    )
    return _process_claimed_jobs_with_attempt_boundary(
        db,
        claimed,
        lease_token=lease_token,
    )
