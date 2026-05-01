from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session, joinedload

from ..enums import ConversationState, EventType, MessageStatus
from ..models import ChannelAccount, OpenClawConversationLink, Ticket, TicketOutboundMessage
from ..settings import get_settings
from ..utils.time import utc_now
from .audit_service import log_event
from .observability import LOGGER
from .openclaw_bridge import dispatch_via_openclaw_bridge, dispatch_via_openclaw_cli, dispatch_via_openclaw_mcp, resolve_channel_account
from .outbound_semantics import external_channel_values, is_external_outbound_message
from .outbound_safety import evaluate_outbound_safety, format_safety_reasons

settings = get_settings()
ALLOWED_OUTBOUND_PROVIDERS = {'openclaw'}


def _external_dispatch_block_reason() -> tuple[str, str] | None:
    if not settings.enable_outbound_dispatch:
        return 'outbound_dispatch_disabled', 'ENABLE_OUTBOUND_DISPATCH=false blocks external dispatch'
    if settings.outbound_provider == 'disabled':
        return 'outbound_provider_disabled', 'OUTBOUND_PROVIDER=disabled blocks external dispatch'
    if settings.outbound_provider not in ALLOWED_OUTBOUND_PROVIDERS:
        return 'unsupported_outbound_provider', f"Unsupported OUTBOUND_PROVIDER: {settings.outbound_provider}"
    return None


def ensure_external_dispatch_allowed() -> None:
    """Fail closed unless the runtime is explicitly configured for external sends."""
    blocked = _external_dispatch_block_reason()
    if blocked:
        _, reason = blocked
        raise RuntimeError(reason)


def _provider_idempotency_key(message: TicketOutboundMessage) -> str:
    return message.provider_message_id or f'nexusdesk-outbound-{message.id}'


def _ensure_provider_idempotency_key(message: TicketOutboundMessage) -> str:
    key = _provider_idempotency_key(message)
    if not message.provider_message_id:
        # The current provider bridge does not yet return a remote provider id before dispatch.
        # Keep a stable local idempotency key in the existing provider_message_id field so retry/recovery
        # can correlate attempts and future bridge implementations can consume the same key.
        message.provider_message_id = key
    return key


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
    _ensure_provider_idempotency_key(message)
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


def requeue_dead_outbound_message(db: Session, *, message_id: int) -> TicketOutboundMessage:
    message = db.query(TicketOutboundMessage).filter(TicketOutboundMessage.id == message_id).first()
    if message is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Outbound message not found')
    if message.status != MessageStatus.dead:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Only dead outbound messages can be requeued')
    if not is_external_outbound_message(message):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Only external outbound messages can be requeued')
    message.status = MessageStatus.pending
    message.provider_status = 'requeued_by_admin'
    message.retry_count = 0
    message.error_message = None
    message.failure_code = None
    message.failure_reason = None
    message.locked_at = None
    message.locked_by = None
    message.next_retry_at = utc_now()
    message.last_attempt_at = None
    _ensure_provider_idempotency_key(message)
    db.flush()
    return message


def claim_pending_messages(db: Session, *, limit: int | None = None, worker_id: str | None = None) -> list[TicketOutboundMessage]:
    worker_id = worker_id or f'worker-{uuid.uuid4().hex[:8]}'
    limit = limit or settings.outbox_batch_size
    now = utc_now()
    lock_deadline = now - timedelta(seconds=settings.outbox_lock_seconds)

    pending_filters = [
        TicketOutboundMessage.channel.in_(external_channel_values()),
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
        db.commit()

    return (
        db.query(TicketOutboundMessage)
        .options(joinedload(TicketOutboundMessage.ticket).joinedload(Ticket.customer), joinedload(TicketOutboundMessage.ticket).joinedload(Ticket.openclaw_link))
        .filter(TicketOutboundMessage.id.in_(claimed_ids))
        .order_by(TicketOutboundMessage.created_at.asc())
        .all()
    )


def _build_fact_evidence(ticket: Ticket | None) -> dict[str, Any] | None:
    if ticket is None or not getattr(ticket, 'tracking_number', None):
        return None
    evidence_summary = ticket.customer_update or ticket.resolution_summary or ticket.last_human_update
    if not evidence_summary:
        return None
    return {
        'evidence_source': 'ticket_operator_context',
        'tracking_number': ticket.tracking_number,
        'checked_at': utc_now().isoformat(),
        'evidence_summary': evidence_summary,
    }


def _enforce_outbound_safety(db: Session, message: TicketOutboundMessage, ticket: Ticket | None) -> bool:
    source = message.provider_status or 'manual_or_unknown'
    fact_evidence = _build_fact_evidence(ticket)
    decision = evaluate_outbound_safety(
        ticket,
        message.body,
        source=source,
        has_fact_evidence=fact_evidence is not None,
        fact_evidence=fact_evidence,
    )
    reason = format_safety_reasons(decision)
    if decision.level == 'block':
        _mark_dead(message, reason, failure_code='safety_blocked')
        log_event(db, ticket_id=message.ticket_id, actor_id=message.created_by, event_type=EventType.outbound_failed, note='Outbound safety gate blocked customer-facing message', payload={'message_id': message.id, 'safety_level': decision.level, 'reasons': decision.reasons})
        return False
    if decision.requires_human_review:
        _mark_review_required(message, reason)
        log_event(db, ticket_id=message.ticket_id, actor_id=message.created_by, event_type=EventType.outbound_failed, note='Outbound safety gate requires human review before send', payload={'message_id': message.id, 'safety_level': decision.level, 'reasons': decision.reasons, 'fact_evidence_present': fact_evidence is not None})
        return False
    message.body = decision.normalized_body
    return True


def process_outbound_message(db: Session, message: TicketOutboundMessage) -> TicketOutboundMessage:
    if message.status == MessageStatus.sent:
        return message
    if not is_external_outbound_message(message):
        _mark_dead(message, 'Non-external outbound row is not eligible for provider dispatch', failure_code='non_external_outbound_not_dispatchable')
        log_event(db, ticket_id=message.ticket_id, actor_id=message.created_by, event_type=EventType.outbound_dead, note='Non-external outbound row was blocked from provider dispatch', payload={'message_id': message.id, 'channel': message.channel.value if hasattr(message.channel, 'value') else str(message.channel), 'provider_status': message.provider_status})
        return message
    blocked = _external_dispatch_block_reason()
    if blocked:
        failure_code, reason = blocked
        _mark_dead(message, reason, failure_code=failure_code)
        log_event(db, ticket_id=message.ticket_id, actor_id=message.created_by, event_type=EventType.outbound_dead, note='External outbound dispatch blocked by runtime kill switch', payload={'message_id': message.id, 'failure_code': failure_code, 'outbound_provider': settings.outbound_provider, 'enable_outbound_dispatch': settings.enable_outbound_dispatch})
        return message
    idempotency_key = _ensure_provider_idempotency_key(message)
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
        log_event(db, ticket_id=message.ticket_id, actor_id=message.created_by, event_type=event_type, note='Queued outbound message could not resolve target', payload={'message_id': message.id, 'idempotency_key': idempotency_key, 'error': message.failure_reason, 'retry_count': message.retry_count})
        return message

    channel_value = link.channel if link is not None and link.channel else message.channel.value
    resolved_channel_account = _resolve_first_send_channel_account(db, ticket, link)
    account_id = link.account_id if link is not None and link.account_id else (resolved_channel_account.account_id if resolved_channel_account else None)
    thread_id = link.thread_id if link is not None else None

    route_context = {
        'channel': channel_value,
        'account_id': account_id,
        'thread_id': thread_id,
        'session_key': session_key,
        'target': target,
        'idempotency_key': idempotency_key,
        'source': 'ticket_or_market_or_fallback',
    }

    if target:
        status_value, provider_status, sent_at = dispatch_via_openclaw_bridge(channel=channel_value, target=target, body=message.body, account_id=account_id, thread_id=thread_id, session_key=session_key)
        if status_value == MessageStatus.failed and settings.openclaw_cli_fallback_enabled:
            LOGGER.warning('openclaw_bridge_dispatch_failed_falling_back_to_cli', extra={'event_payload': {'message_id': message.id, 'ticket_id': message.ticket_id, 'provider_status': provider_status, 'route': route_context}})
            status_value, provider_status, sent_at = dispatch_via_openclaw_cli(channel=channel_value, target=target, body=message.body, account_id=account_id, thread_id=thread_id)
    elif session_key:
        status_value, provider_status, sent_at = dispatch_via_openclaw_mcp(session_key, message.body)
    else:
        status_value, provider_status, sent_at = MessageStatus.failed, 'No target address available', None

    if status_value == MessageStatus.sent:
        _mark_sent(message, provider_status, sent_at)
        if ticket is not None and getattr(ticket, 'conversation_state', None) is not None:
            ticket.conversation_state = ConversationState.waiting_customer
        if session_key:
            log_event(db, ticket_id=message.ticket_id, actor_id=message.created_by, event_type=EventType.openclaw_reply_sent, note='OpenClaw same-route reply sent', payload={'message_id': message.id, 'session_key': session_key, 'provider_status': provider_status, 'idempotency_key': idempotency_key})
        log_event(db, ticket_id=message.ticket_id, actor_id=message.created_by, event_type=EventType.outbound_sent, note='Queued outbound message sent', payload={'message_id': message.id, 'provider_status': provider_status, 'route': route_context})
        return message

    reason = provider_status or 'Dispatch failed'
    _mark_retry(message, reason)
    event_type = EventType.outbound_dead if message.status == MessageStatus.dead else EventType.outbound_retry_scheduled
    log_event(db, ticket_id=message.ticket_id, actor_id=message.created_by, event_type=event_type, note='Queued outbound message failed dispatch', payload={'message_id': message.id, 'error': message.failure_reason, 'retry_count': message.retry_count, 'route': route_context})
    return message


def dispatch_pending_messages(db: Session, *, limit: int | None = None, worker_id: str | None = None) -> list[TicketOutboundMessage]:
    blocked = _external_dispatch_block_reason()
    if blocked:
        failure_code, reason = blocked
        LOGGER.warning('external_outbound_dispatch_blocked_by_runtime_gate', extra={'event_payload': {'failure_code': failure_code, 'reason': reason, 'outbound_provider': settings.outbound_provider, 'enable_outbound_dispatch': settings.enable_outbound_dispatch}})
        return []
    claimed = claim_pending_messages(db, limit=limit, worker_id=worker_id)
    processed: list[TicketOutboundMessage] = []
    for message in claimed:
        process_outbound_message(db, message)
        processed.append(message)
        # Commit after each external dispatch attempt to reduce the window where a sent provider message
        # could be retried after a process crash before a batch-level commit.
        db.commit()
    return processed
