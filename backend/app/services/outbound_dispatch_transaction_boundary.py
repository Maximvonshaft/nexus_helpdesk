from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import or_

_PATCHED = False


def _exception_reason(exc: Exception) -> str:
    return f"Unhandled dispatch exception: {type(exc).__name__}"


def _recover_unhandled_dispatch_exception(db: Any, *, message_id: int, exc: Exception):
    from . import message_dispatch

    message = (
        db.query(message_dispatch.TicketOutboundMessage)
        .filter(message_dispatch.TicketOutboundMessage.id == message_id)
        .first()
    )
    if message is None:
        message_dispatch.LOGGER.warning(
            "outbound_dispatch_exception_recovery_missing_message",
            extra={"event_payload": {"message_id": message_id, "error_type": type(exc).__name__}},
        )
        return None

    reason = _exception_reason(exc)
    message_dispatch._mark_retry(message, reason)
    event_type = (
        message_dispatch.EventType.outbound_dead
        if message.status == message_dispatch.MessageStatus.dead
        else message_dispatch.EventType.outbound_retry_scheduled
    )
    message_dispatch.log_event(
        db,
        ticket_id=message.ticket_id,
        actor_id=message.created_by,
        event_type=event_type,
        note="Queued outbound message failed dispatch with unhandled exception",
        payload={
            "message_id": message.id,
            "error_type": type(exc).__name__,
            "failure_code": message.failure_code,
            "retry_count": message.retry_count,
        },
    )
    message_dispatch.LOGGER.warning(
        "outbound_dispatch_attempt_exception_recovered",
        extra={
            "event_payload": {
                "message_id": message.id,
                "ticket_id": message.ticket_id,
                "error_type": type(exc).__name__,
                "failure_code": message.failure_code,
                "retry_count": message.retry_count,
                "next_status": message.status.value if hasattr(message.status, "value") else str(message.status),
            }
        },
    )
    return message


def reclaim_stale_processing_messages(db: Any, *, limit: int | None = None) -> int:
    """Return expired processing attempts to the canonical retry/dead state machine.

    The stable provider idempotency key is preserved. This closes the crash window
    where a worker had already changed a row to ``processing`` and disappeared
    before persisting a terminal result.
    """
    from . import message_dispatch

    now = message_dispatch.utc_now()
    lock_deadline = now - timedelta(seconds=message_dispatch.settings.outbox_lock_seconds)
    query = (
        db.query(message_dispatch.TicketOutboundMessage)
        .filter(
            message_dispatch.TicketOutboundMessage.channel.in_(
                message_dispatch.external_channel_values()
            ),
            message_dispatch.TicketOutboundMessage.status
            == message_dispatch.MessageStatus.processing,
            or_(
                message_dispatch.TicketOutboundMessage.locked_at.is_(None),
                message_dispatch.TicketOutboundMessage.locked_at < lock_deadline,
            ),
        )
        .order_by(message_dispatch.TicketOutboundMessage.created_at.asc())
        .limit(limit or message_dispatch.settings.outbox_batch_size)
    )
    bind = getattr(db, "bind", None)
    if bind is not None and bind.dialect.name.startswith("postgresql"):
        query = query.with_for_update(skip_locked=True)
    rows = query.all()
    if not rows:
        return 0

    for message in rows:
        message_dispatch._mark_retry(
            message,
            "Previous outbound worker lease expired before a terminal result",
            failure_code="worker_lease_expired",
        )
        event_type = (
            message_dispatch.EventType.outbound_dead
            if message.status == message_dispatch.MessageStatus.dead
            else message_dispatch.EventType.outbound_retry_scheduled
        )
        message_dispatch.log_event(
            db,
            ticket_id=message.ticket_id,
            actor_id=message.created_by,
            event_type=event_type,
            note="Expired outbound processing attempt was recovered",
            payload={
                "message_id": message.id,
                "failure_code": message.failure_code,
                "retry_count": message.retry_count,
                "previous_worker": getattr(message, "locked_by", None),
            },
        )
    db.commit()
    message_dispatch.LOGGER.warning(
        "outbound_stale_processing_recovered",
        extra={"event_payload": {"count": len(rows)}},
    )
    return len(rows)


def _dispatch_pending_messages_with_attempt_boundary(
    db: Any,
    *,
    limit: int | None = None,
    worker_id: str | None = None,
):
    from . import message_dispatch

    blocked = message_dispatch._external_dispatch_block_reason()
    if blocked:
        failure_code, reason = blocked
        message_dispatch.LOGGER.warning(
            "external_outbound_dispatch_blocked_by_runtime_gate",
            extra={
                "event_payload": {
                    "failure_code": failure_code,
                    "reason": reason,
                    "outbound_provider": message_dispatch.settings.outbound_provider,
                    "enable_outbound_dispatch": message_dispatch.settings.enable_outbound_dispatch,
                }
            },
        )
        return []

    reclaim_stale_processing_messages(db, limit=limit)
    claimed = message_dispatch.claim_pending_messages(db, limit=limit, worker_id=worker_id)
    processed: list[Any] = []
    for message in claimed:
        message_id = message.id
        try:
            message_dispatch.process_outbound_message(db, message)
        except Exception as exc:
            db.rollback()
            recovered = _recover_unhandled_dispatch_exception(db, message_id=message_id, exc=exc)
            if recovered is not None:
                db.commit()
                processed.append(recovered)
            continue
        processed.append(message)
        # Commit after each external dispatch attempt to retain the stable
        # idempotency boundary and prevent one failure from aborting the batch.
        db.commit()
    return processed


def apply_outbound_dispatch_transaction_boundary_patch() -> None:
    global _PATCHED
    if _PATCHED:
        return

    from . import message_dispatch

    message_dispatch.dispatch_pending_messages = _dispatch_pending_messages_with_attempt_boundary
    _PATCHED = True
