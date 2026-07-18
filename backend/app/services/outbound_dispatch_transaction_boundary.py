from __future__ import annotations

import uuid
from contextlib import nullcontext
from datetime import timedelta
from typing import Any

from sqlalchemy import or_, update


def _exception_reason(exc: Exception) -> str:
    return f"Unhandled dispatch exception: {type(exc).__name__}"


def _is_sqlalchemy_session(db: Any) -> bool:
    return hasattr(db, "execute") and getattr(db, "bind", None) is not None


def _claim_token(worker_id: str | None) -> str:
    prefix = (worker_id or "outbound-worker").strip() or "outbound-worker"
    return f"{prefix[:80]}:{uuid.uuid4().hex}"


def _refresh_message_lease(
    db: Any,
    *,
    message_id: int,
    lease_token: str,
) -> bool:
    if not _is_sqlalchemy_session(db):
        return True

    from . import message_dispatch

    now = message_dispatch.utc_now()
    result = db.execute(
        update(message_dispatch.TicketOutboundMessage)
        .where(
            message_dispatch.TicketOutboundMessage.id == message_id,
            message_dispatch.TicketOutboundMessage.status
            == message_dispatch.MessageStatus.processing,
            message_dispatch.TicketOutboundMessage.locked_by == lease_token,
        )
        .values(locked_at=now)
    )
    if result.rowcount != 1:
        db.rollback()
        message_dispatch.LOGGER.warning(
            "outbound_message_lease_refresh_rejected",
            extra={"event_payload": {"message_id": message_id}},
        )
        return False
    db.commit()
    return True


def _owns_message_lease(
    db: Any,
    *,
    message_id: int,
    lease_token: str,
) -> bool:
    if not _is_sqlalchemy_session(db):
        return True

    from . import message_dispatch

    no_autoflush = getattr(db, "no_autoflush", nullcontext())
    with no_autoflush:
        row = (
            db.query(
                message_dispatch.TicketOutboundMessage.locked_by,
                message_dispatch.TicketOutboundMessage.status,
            )
            .filter(message_dispatch.TicketOutboundMessage.id == message_id)
            .first()
        )
    if row is None:
        return False
    return (
        row[0] == lease_token
        and row[1] == message_dispatch.MessageStatus.processing
    )


def _recover_unhandled_dispatch_exception(
    db: Any,
    *,
    message_id: int,
    lease_token: str,
    exc: Exception,
):
    from . import message_dispatch

    if not _owns_message_lease(
        db,
        message_id=message_id,
        lease_token=lease_token,
    ):
        message_dispatch.LOGGER.warning(
            "outbound_stale_exception_result_rejected",
            extra={
                "event_payload": {
                    "message_id": message_id,
                    "error_type": type(exc).__name__,
                }
            },
        )
        return None

    message = (
        db.query(message_dispatch.TicketOutboundMessage)
        .filter(message_dispatch.TicketOutboundMessage.id == message_id)
        .first()
    )
    if message is None:
        message_dispatch.LOGGER.warning(
            "outbound_dispatch_exception_recovery_missing_message",
            extra={
                "event_payload": {
                    "message_id": message_id,
                    "error_type": type(exc).__name__,
                }
            },
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
                "next_status": (
                    message.status.value
                    if hasattr(message.status, "value")
                    else str(message.status)
                ),
            }
        },
    )
    return message


def reclaim_stale_processing_messages(
    db: Any,
    *,
    limit: int | None = None,
) -> int:
    """Return expired processing attempts to the canonical retry/dead state machine."""
    from . import message_dispatch

    now = message_dispatch.utc_now()
    lock_deadline = now - timedelta(
        seconds=message_dispatch.settings.outbox_lock_seconds
    )
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
        previous_worker = getattr(message, "locked_by", None)
        previous_locked_at = getattr(message, "locked_at", None)
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
                "previous_worker": previous_worker,
                "previous_locked_at": previous_locked_at,
            },
        )
    db.commit()
    message_dispatch.LOGGER.warning(
        "outbound_stale_processing_recovered",
        extra={"event_payload": {"count": len(rows)}},
    )
    return len(rows)


def dispatch_pending_messages(
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
                    "enable_outbound_dispatch": (
                        message_dispatch.settings.enable_outbound_dispatch
                    ),
                }
            },
        )
        return []

    reclaim_stale_processing_messages(db, limit=limit)
    lease_token = _claim_token(worker_id)
    claimed = message_dispatch.claim_pending_messages(
        db,
        limit=limit,
        worker_id=lease_token,
    )
    processed: list[Any] = []
    for message in claimed:
        message_id = message.id
        if not _refresh_message_lease(
            db,
            message_id=message_id,
            lease_token=lease_token,
        ):
            continue
        try:
            message_dispatch.process_outbound_message(db, message)
            if not _owns_message_lease(
                db,
                message_id=message_id,
                lease_token=lease_token,
            ):
                db.rollback()
                message_dispatch.LOGGER.warning(
                    "outbound_stale_completion_rejected",
                    extra={"event_payload": {"message_id": message_id}},
                )
                continue
        except Exception as exc:
            db.rollback()
            recovered = _recover_unhandled_dispatch_exception(
                db,
                message_id=message_id,
                lease_token=lease_token,
                exc=exc,
            )
            if recovered is not None:
                db.commit()
                processed.append(recovered)
            continue
        processed.append(message)
        db.commit()
    return processed
