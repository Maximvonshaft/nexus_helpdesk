from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session, joinedload

from ..enums import ConversationState, EventType, MessageStatus
from ..models import OpenClawConversationLink, Ticket, TicketOutboundMessage
from ..settings import get_settings
from ..utils.time import utc_now
from .audit_service import log_event
from .observability import LOGGER
from .openclaw_bridge import dispatch_via_openclaw_bridge, dispatch_via_openclaw_cli, dispatch_via_openclaw_mcp

settings = get_settings()


def queue_outbound_message(
    db: Session,
    *,
    ticket_id: int,
    channel,
    body: str,
    created_by: int | None,
    provider_status: str = 'queued',
) -> TicketOutboundMessage:
    message = TicketOutboundMessage(
        ticket_id=ticket_id,
        channel=channel,
        status=MessageStatus.pending,
        body=body,
        provider_status=provider_status,
        created_by=created_by,
        max_retries=settings.outbox_max_retries,
    )
    db.add(message)
    db.flush()
    return message



def _mark_retry(message: TicketOutboundMessage, reason: str) -> None:
    message.retry_count += 1
    message.error_message = reason
    message.failure_reason = reason
    message.last_attempt_at = utc_now()
    message.locked_at = None
    message.locked_by = None
    backoff_minutes = min(2 ** max(message.retry_count - 1, 0), 30)
    if message.retry_count >= message.max_retries:
        message.status = MessageStatus.dead
        message.provider_status = 'dead:max_retries'
        message.failure_code = 'max_retries'
        message.next_retry_at = None
    else:
        message.status = MessageStatus.pending
        message.provider_status = f'retry_scheduled:{backoff_minutes}m'
        message.failure_code = 'retryable_dispatch_error'
        message.next_retry_at = utc_now() + timedelta(minutes=backoff_minutes)


def _mark_dead(message: TicketOutboundMessage, reason: str, *, failure_code: str) -> None:
    message.status = MessageStatus.dead
    message.provider_status = f'dead:{failure_code}'
    message.error_message = reason
    message.failure_code = failure_code
    message.failure_reason = reason
    message.last_attempt_at = utc_now()
    message.next_retry_at = None
    message.locked_at = None
    message.locked_by = None


def _mark_sent(message: TicketOutboundMessage, provider_status: str | None, sent_at) -> None:
    message.status = MessageStatus.sent
    message.provider_status = provider_status
    message.sent_at = sent_at
    message.error_message = None
    message.failure_code = None
    message.failure_reason = None
    message.last_attempt_at = utc_now()
    message.next_retry_at = None
    message.locked_at = None
    message.locked_by = None


def claim_pending_messages(db: Session, *, limit: int | None = None, worker_id: str | None = None) -> list[TicketOutboundMessage]:
    worker_id = worker_id or f'worker-{uuid.uuid4().hex[:8]}'
    limit = limit or settings.outbox_batch_size
    now = utc_now()
    lock_deadline = now - timedelta(seconds=settings.outbox_lock_seconds)

    if db.bind and db.bind.dialect.name.startswith('postgresql'):
        rows = db.execute(
            select(TicketOutboundMessage.id)
            .where(
                TicketOutboundMessage.status == MessageStatus.pending,
                or_(TicketOutboundMessage.next_retry_at.is_(None), TicketOutboundMessage.next_retry_at <= now),
                or_(TicketOutboundMessage.locked_at.is_(None), TicketOutboundMessage.locked_at < lock_deadline),
            )
            .order_by(TicketOutboundMessage.created_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        claimed_ids = [row[0] for row in rows]
        if not claimed_ids:
            db.rollback()
            return []
        db.execute(
            update(TicketOutboundMessage)
            .where(TicketOutboundMessage.id.in_(claimed_ids))
            .values(status=MessageStatus.processing, locked_at=now, locked_by=worker_id)
        )
        db.commit()
    else:
        candidate_ids = [
            row[0]
            for row in (
                db.query(TicketOutboundMessage.id)
                .filter(
                    TicketOutboundMessage.status == MessageStatus.pending,
                    or_(TicketOutboundMessage.next_retry_at.is_(None), TicketOutboundMessage.next_retry_at <= now),
                    or_(TicketOutboundMessage.locked_at.is_(None), TicketOutboundMessage.locked_at < lock_deadline),
                )
                .order_by(TicketOutboundMessage.created_at.asc())
                .limit(limit)
                .all()
            )
        ]
        claimed_ids: list[int] = []
        for message_id in candidate_ids:
            updated = db.execute(
                update(TicketOutboundMessage)
                .where(
                    TicketOutboundMessage.id == message_id,
                    TicketOutboundMessage.status == MessageStatus.pending,
                    or_(TicketOutboundMessage.next_retry_at.is_(None), TicketOutboundMessage.next_retry_at <= now),
                    or_(TicketOutboundMessage.locked_at.is_(None), TicketOutboundMessage.locked_at < lock_deadline),
                )
                .values(status=MessageStatus.processing, locked_at=now, locked_by=worker_id)
            )
            if updated.rowcount == 1:
                claimed_ids.append(message_id)
        if not claimed_ids:
            db.rollback()
            return []
        db.commit()

    return (
        db.query(TicketOutboundMessage)
        .options(joinedload(TicketOutboundMessage.ticket).joinedload(Ticket.customer), joinedload(TicketOutboundMessage.ticket).joinedload(Ticket.openclaw_link))
        .filter(TicketOutboundMessage.id.in_(claimed_ids))
        .order_by(TicketOutboundMessage.created_at.asc())
        .all()
    )


def process_outbound_message(db: Session, message: TicketOutboundMessage) -> TicketOutboundMessage:
    ticket = message.ticket
    target = None
    session_key = None
    link = None
    if ticket is not None:
        target = ticket.source_chat_id or ticket.preferred_reply_contact or (ticket.customer.phone if ticket.customer else None)
        if ticket.openclaw_link is not None:
            link = ticket.openclaw_link
            session_key = link.session_key
            target = link.recipient or target

    if not target and not session_key:
        _mark_retry(message, 'No target address available')
        if message.status == MessageStatus.dead:
            log_event(db, ticket_id=message.ticket_id, actor_id=message.created_by, event_type=EventType.outbound_dead, note='Queued outbound message permanently failed', payload={'message_id': message.id, 'error': message.failure_reason})
        else:
            log_event(db, ticket_id=message.ticket_id, actor_id=message.created_by, event_type=EventType.outbound_retry_scheduled, note='Queued outbound message scheduled for retry', payload={'message_id': message.id, 'error': message.failure_reason, 'retry_count': message.retry_count})
        return message

    status = None
    provider_status = None
    sent_at = None

    channel_value = link.channel if link is not None and link.channel else message.channel.value
    account_id = link.account_id if link is not None else None
    thread_id = link.thread_id if link is not None else None

    if target:
        status, provider_status, sent_at = dispatch_via_openclaw_bridge(
            channel=channel_value,
            target=target,
            body=message.body,
            account_id=account_id,
            thread_id=thread_id,
            session_key=session_key,
        )
        if status == MessageStatus.failed and settings.openclaw_cli_fallback_enabled:
            LOGGER.warning(
                'openclaw_bridge_dispatch_failed_falling_back_to_cli',
                extra={'event_payload': {
                    'message_id': message.id,
                    'ticket_id': message.ticket_id,
                    'channel': channel_value,
                    'target': target,
                    'provider_status': provider_status,
                }},
            )
            status, provider_status, sent_at = dispatch_via_openclaw_cli(
                channel=channel_value,
                target=target,
                body=message.body,
                account_id=account_id,
                thread_id=thread_id,
            )
    elif session_key:
        status, provider_status, sent_at = dispatch_via_openclaw_mcp(session_key, message.body)
    else:
        status, provider_status, sent_at = MessageStatus.failed, 'No target address available', None

    if status == MessageStatus.sent:
        _mark_sent(message, provider_status, sent_at)
        if ticket is not None and getattr(ticket, 'conversation_state', None) is not None:
            ticket.conversation_state = ConversationState.waiting_customer
        if session_key:
            log_event(
                db,
                ticket_id=message.ticket_id,
                actor_id=message.created_by,
                event_type=EventType.openclaw_reply_sent,
                note='OpenClaw same-route reply sent',
                payload={'message_id': message.id, 'session_key': session_key, 'provider_status': provider_status},
            )
        log_event(
            db,
            ticket_id=message.ticket_id,
            actor_id=message.created_by,
            event_type=EventType.outbound_sent,
            note='Queued outbound message sent',
            payload={'message_id': message.id, 'provider_status': provider_status},
        )
        return message

    reason = provider_status or 'Dispatch failed'
    _mark_retry(message, reason)
    if message.status == MessageStatus.dead:
        log_event(
            db,
            ticket_id=message.ticket_id,
            actor_id=message.created_by,
            event_type=EventType.outbound_dead,
            note='Queued outbound message permanently failed',
            payload={'message_id': message.id, 'error': message.failure_reason},
        )
    else:
        log_event(
            db,
            ticket_id=message.ticket_id,
            actor_id=message.created_by,
            event_type=EventType.outbound_retry_scheduled,
            note='Queued outbound message scheduled for retry',
            payload={'message_id': message.id, 'error': message.failure_reason, 'retry_count': message.retry_count},
        )
    return message


def dispatch_pending_messages(db: Session, *, limit: int | None = None, worker_id: str | None = None) -> list[TicketOutboundMessage]:
    claimed = claim_pending_messages(db, limit=limit, worker_id=worker_id)
    processed: list[TicketOutboundMessage] = []
    for message in claimed:
        process_outbound_message(db, message)
        processed.append(message)
    db.commit()
    return processed
