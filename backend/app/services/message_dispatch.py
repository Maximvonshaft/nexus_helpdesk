from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session, joinedload

from ..enums import ConversationState, EventType, MessageStatus
from ..models import ChannelAccount, OpenClawConversationLink, Ticket, TicketOutboundMessage
from ..settings import get_settings
from ..utils.time import utc_now
from .audit_service import log_event
from .observability import LOGGER
from .openclaw_bridge import dispatch_via_openclaw_bridge, dispatch_via_openclaw_cli, dispatch_via_openclaw_mcp, resolve_channel_account
from .outbound_safety import evaluate_outbound_safety, format_safety_reasons

settings = get_settings()


def _resolve_first_send_channel_account(db: Session, ticket: Ticket | None, link: OpenClawConversationLink | None) -> ChannelAccount | None:
    if link is not None and getattr(link, 'channel_account_id', None):
        return db.query(ChannelAccount).filter(ChannelAccount.id == link.channel_account_id, ChannelAccount.is_active.is_(True)).first()
    if ticket is not None and getattr(ticket, 'channel_account_id', None):
        row = db.query(ChannelAccount).filter(ChannelAccount.id == ticket.channel_account_id, ChannelAccount.is_active.is_(True)).first()
        if row:
            return row
    if ticket is not None:
        return resolve_channel_account(db, market_id=ticket.market_id, account_id=None)
    return None


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


def _mark_review_required(message: TicketOutboundMessage, reason: str) -> None:
    message.status = MessageStatus.draft
    message.provider_status = 'safety_review_required'
    message.error_message = reason
    message.failure_code = 'safety_review_required'
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

    pending_filters = [
        TicketOutboundMessage.status == MessageStatus.pending,
        or_(TicketOutboundMessage.next_retry_at.is_(None), TicketOutboundMessage.next_retry_at <= now),
        or_(TicketOutboundMessage.locked_at.is_(None), TicketOutboundMessage.locked_at < lock_deadline),
    ]

    if db.bind and db.bind.dialect.name.startswith('postgresql'):
        rows = db.execute(
            select(TicketOutboundMessage.id)
            .where(*pending_filters)
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
        # Queue claim is intentionally committed separately so other workers cannot double-claim messages.
        db.commit()
    else:
        candidate_ids = [row[0] for row in db.query(TicketOutboundMessage.id).filter(*pending_filters).order_by(TicketOutboundMessage.created_at.asc()).limit(limit).all()]
        claimed_ids: list[int] = []
        for message_id in candidate_ids:
            updated = db.execute(
                update(TicketOutboundMessage)
                .where(TicketOutboundMessage.id == message_id, *pending_filters)
                .values(status=MessageStatus.processing, locked_at=now, locked_by=worker_id)
            )
            if updated.rowcount == 1:
                claimed_ids.append(message_id)
        if not claimed_ids:
            db.rollback()
            return []
        # Queue claim is intentionally committed separately so other workers cannot double-claim messages.
        db.commit()

    return (
        db.query(TicketOutboundMessage)
        .options(joinedload(TicketOutboundMessage.ticket).joinedload(Ticket.customer), joinedload(TicketOutboundMessage.ticket).joinedload(Ticket.openclaw_link))
        .filter(TicketOutboundMessage.id.in_(claimed_ids))
        .order_by(TicketOutboundMessage.created_at.asc())
        .all()
    )


def _enforce_outbound_safety(db: Session, message: TicketOutboundMessage, ticket: Ticket | None) -> bool:
    source = message.provider_status or 'manual_or_unknown'
    decision = evaluate_outbound_safety(ticket, message.body, source=source, has_fact_evidence=False)
    reason = format_safety_reasons(decision)
    if decision.level == 'block':
        _mark_dead(message, reason, failure_code='safety_blocked')
        log_event(
            db,
            ticket_id=message.ticket_id,
            actor_id=message.created_by,
            event_type=EventType.outbound_failed,
            note='Outbound safety gate blocked customer-facing message',
            payload={'message_id': message.id, 'safety_level': decision.level, 'reasons': decision.reasons},
        )
        return False
    if decision.requires_human_review:
        _mark_review_required(message, reason)
        log_event(
            db,
            ticket_id=message.ticket_id,
            actor_id=message.created_by,
            event_type=EventType.outbound_failed,
            note='Outbound safety gate requires human review before send',
            payload={'message_id': message.id, 'safety_level': decision.level, 'reasons': decision.reasons},
        )
        return False
    message.body = decision.normalized_body
    return True


def process_outbound_message(db: Session, message: TicketOutboundMessage) -> TicketOutboundMessage:
    ticket = message.ticket
    if not _enforce_outbound_safety(db, message, ticket):
        return message

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
        event_type = EventType.outbound_dead if message.status == MessageStatus.dead else EventType.outbound_retry_scheduled
        log_event(db, ticket_id=message.ticket_id, actor_id=message.created_by, event_type=event_type, note='Queued outbound message could not resolve target', payload={'message_id': message.id, 'error': message.failure_reason, 'retry_count': message.retry_count})
        return message

    channel_value = link.channel if link is not None and link.channel else message.channel.value
    resolved_channel_account = _resolve_first_send_channel_account(db, ticket, link)
    account_id = link.account_id if link is not None and link.account_id else (resolved_channel_account.account_id if resolved_channel_account else None)
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
            LOGGER.warning('openclaw_bridge_dispatch_failed_falling_back_to_cli', extra={'event_payload': {'message_id': message.id, 'ticket_id': message.ticket_id, 'channel': channel_value, 'target': target, 'provider_status': provider_status}})
            status, provider_status, sent_at = dispatch_via_openclaw_cli(channel=channel_value, target=target, body=message.body, account_id=account_id, thread_id=thread_id)
    elif session_key:
        status, provider_status, sent_at = dispatch_via_openclaw_mcp(session_key, message.body)
    else:
        status, provider_status, sent_at = MessageStatus.failed, 'No target address available', None

    if status == MessageStatus.sent:
        _mark_sent(message, provider_status, sent_at)
        if ticket is not None and getattr(ticket, 'conversation_state', None) is not None:
            ticket.conversation_state = ConversationState.waiting_customer
        if session_key:
            log_event(db, ticket_id=message.ticket_id, actor_id=message.created_by, event_type=EventType.openclaw_reply_sent, note='OpenClaw same-route reply sent', payload={'message_id': message.id, 'session_key': session_key, 'provider_status': provider_status})
        log_event(db, ticket_id=message.ticket_id, actor_id=message.created_by, event_type=EventType.outbound_sent, note='Queued outbound message sent', payload={'message_id': message.id, 'provider_status': provider_status, 'route': {'channel': channel_value, 'account_id': account_id, 'source': 'ticket_or_market_or_fallback'}})
        return message

    reason = provider_status or 'Dispatch failed'
    _mark_retry(message, reason)
    event_type = EventType.outbound_dead if message.status == MessageStatus.dead else EventType.outbound_retry_scheduled
    log_event(db, ticket_id=message.ticket_id, actor_id=message.created_by, event_type=event_type, note='Queued outbound message failed dispatch', payload={'message_id': message.id, 'error': message.failure_reason, 'retry_count': message.retry_count, 'route': {'channel': channel_value, 'account_id': account_id, 'source': 'ticket_or_market_or_fallback'}})
    return message


def dispatch_pending_messages(db: Session, *, limit: int | None = None, worker_id: str | None = None) -> list[TicketOutboundMessage]:
    claimed = claim_pending_messages(db, limit=limit, worker_id=worker_id)
    processed: list[TicketOutboundMessage] = []
    for message in claimed:
        process_outbound_message(db, message)
        processed.append(message)
    db.commit()
    return processed
