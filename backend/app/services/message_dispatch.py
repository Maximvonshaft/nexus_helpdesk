from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session, joinedload

from ..enums import ConversationState, EventType, MessageStatus, SourceChannel
from ..models import ChannelAccount, ExternalChannelConversationLink, Ticket, TicketOutboundAttachment, TicketOutboundMessage
from ..settings import get_settings
from ..utils.time import utc_now
from .audit_service import log_event
from .email_mailbox_identity import ensure_outbound_mailbox_identity
from .observability import LOGGER
from .ai_reply_contract import (
    AI_ORIGINS,
    AI_REPLY_CONTRACT_V2,
    FORBIDDEN_CUSTOMER_VISIBLE_ORIGINS,
    HUMAN_ORIGIN,
    HUMAN_REPLY_STATES,
    validate_ai_reply_contract,
)
from .external_channel_bridge import dispatch_via_external_channel_bridge, dispatch_via_external_channel_cli, dispatch_via_external_channel_mcp
from .outbound_adapters.email import dispatch_email_outbound
from .outbound_adapters.whatsapp_native import dispatch_whatsapp_native_outbound
from .outbound_semantics import external_channel_values, is_external_outbound_message
from .outbound_safety import evaluate_outbound_safety, format_safety_reasons

settings = get_settings()
ALLOWED_OUTBOUND_PROVIDERS = {'native', 'smtp', 'email'}


def _external_dispatch_block_reason() -> tuple[str, str] | None:
    if not settings.enable_outbound_dispatch:
        return 'outbound_dispatch_disabled', 'ENABLE_OUTBOUND_DISPATCH=false blocks external dispatch'
    if settings.outbound_provider == 'disabled':
        return 'outbound_provider_disabled', 'OUTBOUND_PROVIDER=disabled blocks external dispatch'
    if settings.outbound_provider not in ALLOWED_OUTBOUND_PROVIDERS:
        return 'unsupported_outbound_provider', f"Unsupported OUTBOUND_PROVIDER: {settings.outbound_provider}"
    return None


def _resolve_channel_account(db: Session, *, market_id: int | None, account_id: str | None) -> ChannelAccount | None:
    if account_id:
        row = (
            db.query(ChannelAccount)
            .filter(
                ChannelAccount.account_id == account_id,
                ChannelAccount.provider.in_([SourceChannel.whatsapp.value, SourceChannel.telegram.value, SourceChannel.sms.value]),
                ChannelAccount.is_active.is_(True),
            )
            .first()
        )
        if row is not None:
            return row
    query = db.query(ChannelAccount).filter(
        ChannelAccount.provider.in_([SourceChannel.whatsapp.value, SourceChannel.telegram.value, SourceChannel.sms.value]),
        ChannelAccount.is_active.is_(True),
    )
    if market_id is not None:
        row = query.filter(ChannelAccount.market_id == market_id).order_by(ChannelAccount.priority.asc(), ChannelAccount.id.asc()).first()
        if row is not None:
            return row
    return query.filter(ChannelAccount.market_id.is_(None)).order_by(ChannelAccount.priority.asc(), ChannelAccount.id.asc()).first()


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


def _resolve_first_send_channel_account(db: Session, ticket: Ticket | None, link: ExternalChannelConversationLink | None) -> ChannelAccount | None:
    if link is not None and getattr(link, 'channel_account_id', None):
        return db.query(ChannelAccount).filter(ChannelAccount.id == link.channel_account_id, ChannelAccount.is_active.is_(True)).first()
    if ticket is not None and getattr(ticket, 'channel_account_id', None):
        row = db.query(ChannelAccount).filter(ChannelAccount.id == ticket.channel_account_id, ChannelAccount.is_active.is_(True)).first()
        if row:
            return row
    if ticket is not None:
        return _resolve_channel_account(db, market_id=ticket.market_id, account_id=None)
    return None


def queue_outbound_message(
    db: Session,
    *,
    ticket_id: int,
    channel,
    body: str,
    created_by: int | None,
    subject: str | None = None,
    provider_status: str = 'queued',
    origin: str | None = None,
    runtime_trace_id: str | None = None,
    runtime_contract_version: str | None = None,
    runtime_signature: str | None = None,
    safety_status: str | None = None,
) -> TicketOutboundMessage:
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    normalized_origin = _normalize_customer_visible_origin(origin, created_by=created_by)
    _enforce_customer_visible_origin(
        body=body,
        origin=normalized_origin,
        ticket=ticket,
        created_by=created_by,
        runtime_trace_id=runtime_trace_id,
        runtime_contract_version=runtime_contract_version,
        runtime_signature=runtime_signature,
        safety_status=safety_status,
    )
    message = TicketOutboundMessage(
        ticket_id=ticket_id,
        channel=channel,
        status=MessageStatus.pending,
        subject=subject,
        body=body,
        origin=normalized_origin,
        runtime_trace_id=runtime_trace_id,
        runtime_contract_version=runtime_contract_version,
        runtime_signature=runtime_signature,
        safety_status=safety_status,
        provider_status=provider_status,
        created_by=created_by,
        max_retries=settings.outbox_max_retries,
    )
    db.add(message)
    db.flush()
    _ensure_provider_idempotency_key(message)
    channel_value = channel.value if hasattr(channel, "value") else str(channel)
    if channel_value == SourceChannel.email.value:
        ensure_outbound_mailbox_identity(message, include_message_id=True)
    db.flush()
    return message


def _normalize_customer_visible_origin(origin: str | None, *, created_by: int | None) -> str:
    cleaned = (origin or "").strip().lower()
    if cleaned:
        return cleaned
    return HUMAN_ORIGIN if created_by is not None else "business_system"


def _enforce_customer_visible_origin(
    *,
    body: str,
    origin: str,
    ticket: Ticket | None,
    created_by: int | None,
    runtime_trace_id: str | None,
    runtime_contract_version: str | None,
    runtime_signature: str | None,
    safety_status: str | None,
) -> None:
    if origin in FORBIDDEN_CUSTOMER_VISIBLE_ORIGINS:
        raise ValueError(f"{origin}_cannot_queue_customer_visible_text")
    if origin in AI_ORIGINS:
        if ticket is not None and ticket.conversation_state in HUMAN_REPLY_STATES:
            raise ValueError("human_active_blocks_ai_autoreply")
        violation = validate_ai_reply_contract(
            body=body,
            runtime_trace_id=runtime_trace_id,
            contract_version=runtime_contract_version,
            runtime_signature=runtime_signature,
            safety_status=safety_status,
        )
        if violation:
            raise ValueError(violation)
        return
    if origin == HUMAN_ORIGIN:
        if created_by is None:
            raise ValueError("human_agent_origin_requires_actor")
        if ticket is not None and ticket.conversation_state == ConversationState.ai_active:
            raise ValueError("human_agent_reply_requires_handoff_or_takeover")
        return
    raise ValueError("unsupported_customer_visible_origin")


def _mark_origin_blocked(message: TicketOutboundMessage, reason: str) -> None:
    _mark_dead(message, reason, failure_code=reason)


def _mark_retry(message: TicketOutboundMessage, reason: str, *, failure_code: str | None = None) -> None:
    message.retry_count += 1
    message.error_message = reason
    message.failure_reason = reason
    message.last_attempt_at = utc_now()
    message.locked_at = None
    message.locked_by = None
    backoff_minutes = min(2 ** max(message.retry_count - 1, 0), 30)
    if message.retry_count >= message.max_retries:
        message.status = MessageStatus.dead
        message.failure_code = failure_code or 'max_retries'
        message.provider_status = f'dead:{message.failure_code}'
        message.next_retry_at = None
    else:
        message.status = MessageStatus.pending
        message.failure_code = failure_code or 'retryable_dispatch_error'
        suffix = f':{message.failure_code}' if failure_code else ''
        message.provider_status = f'retry_scheduled:{backoff_minutes}m{suffix}'
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
        .options(
            joinedload(TicketOutboundMessage.ticket).joinedload(Ticket.customer),
            joinedload(TicketOutboundMessage.ticket).joinedload(Ticket.external_channel_link),
            joinedload(TicketOutboundMessage.attachment_links).joinedload(TicketOutboundAttachment.attachment),
        )
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


def _dispatch_whatsapp_message(db: Session, message: TicketOutboundMessage, ticket: Ticket | None, idempotency_key: str) -> tuple[MessageStatus, str | None, object | None, dict[str, Any]]:
    try:
        if settings.whatsapp_dispatch_mode == 'native_sidecar':
            return dispatch_whatsapp_native_outbound(db, message=message, ticket=ticket, idempotency_key=idempotency_key)
        if settings.whatsapp_dispatch_mode == 'cloud_api_future':
            return MessageStatus.failed, 'whatsapp_cloud_api_not_implemented', None, {
                'channel': SourceChannel.whatsapp.value,
                'adapter': 'whatsapp_cloud_api_future',
                'idempotency_key': idempotency_key,
                'failure_code': 'whatsapp_cloud_api_not_implemented',
                'error': 'WhatsApp Cloud API dispatch mode is reserved but not implemented',
                'retryable': False,
            }
        if settings.whatsapp_dispatch_mode == 'external_channel_bridge':
            return MessageStatus.failed, 'legacy_external_channel_bridge_retired', None, {
                'channel': SourceChannel.whatsapp.value,
                'adapter': 'legacy_external_channel_bridge_retired',
                'idempotency_key': idempotency_key,
                'failure_code': 'legacy_external_channel_bridge_retired',
                'error': 'ExternalChannel bridge dispatch has been retired; use WHATSAPP_DISPATCH_MODE=native_sidecar',
                'retryable': False,
            }
        if settings.whatsapp_dispatch_mode != 'disabled':
            return MessageStatus.failed, 'unsupported_whatsapp_dispatch_mode', None, {
                'channel': SourceChannel.whatsapp.value,
                'adapter': 'unsupported_whatsapp_dispatch_mode',
                'idempotency_key': idempotency_key,
                'failure_code': 'unsupported_whatsapp_dispatch_mode',
                'error': f'Unsupported WHATSAPP_DISPATCH_MODE: {settings.whatsapp_dispatch_mode}',
                'retryable': False,
            }
        return MessageStatus.failed, 'whatsapp_dispatch_disabled', None, {
            'channel': SourceChannel.whatsapp.value,
            'adapter': 'whatsapp_dispatch_disabled',
            'idempotency_key': idempotency_key,
            'failure_code': 'whatsapp_dispatch_disabled',
            'error': 'WHATSAPP_DISPATCH_MODE=disabled blocks WhatsApp dispatch',
            'retryable': False,
        }
    except ValueError as exc:
        error_code = str(exc)
        return MessageStatus.failed, error_code, None, {
            'channel': SourceChannel.whatsapp.value,
            'adapter': 'whatsapp_route_resolution',
            'idempotency_key': idempotency_key,
            'error': error_code,
        }


def _dispatch_email_message(db: Session, message: TicketOutboundMessage, ticket: Ticket | None, idempotency_key: str) -> tuple[MessageStatus, str | None, object | None, dict[str, Any]]:
    try:
        ensure_outbound_mailbox_identity(message, ticket=ticket, include_message_id=True)
        db.flush()
        return dispatch_email_outbound(db, message=message, ticket=ticket, idempotency_key=idempotency_key)
    except ValueError as exc:
        error_code = str(exc)
        return MessageStatus.failed, error_code, None, {
            'channel': SourceChannel.email.value,
            'adapter': 'smtp',
            'idempotency_key': idempotency_key,
            'failure_code': error_code,
            'error': error_code,
        }


def _handle_dispatch_result(
    db: Session,
    *,
    message: TicketOutboundMessage,
    ticket: Ticket | None,
    status_value: MessageStatus,
    provider_status: str | None,
    sent_at,
    route_context: dict[str, Any],
) -> TicketOutboundMessage:
    session_key = route_context.get('session_key')
    if status_value == MessageStatus.sent:
        _mark_sent(message, provider_status, sent_at)
        if ticket is not None and getattr(ticket, 'conversation_state', None) is not None:
            ticket.conversation_state = ConversationState.waiting_customer
        if session_key:
            log_event(db, ticket_id=message.ticket_id, actor_id=message.created_by, event_type=EventType.external_channel_reply_sent, note='Legacy same-route reply sent', payload={'message_id': message.id, 'session_key': session_key, 'provider_status': provider_status, 'idempotency_key': route_context.get('idempotency_key')})
        log_event(db, ticket_id=message.ticket_id, actor_id=message.created_by, event_type=EventType.outbound_sent, note='Queued outbound message sent', payload={'message_id': message.id, 'provider_status': provider_status, 'route': route_context})
        return message

    route_error = route_context.get('error')
    reason = route_error if isinstance(route_error, str) and route_error else (provider_status or 'Dispatch failed')
    route_failure_code = route_context.get('failure_code')
    if route_context.get('retryable') is False:
        _mark_dead(message, reason, failure_code=route_failure_code if isinstance(route_failure_code, str) else 'non_retryable_dispatch_error')
        log_event(db, ticket_id=message.ticket_id, actor_id=message.created_by, event_type=EventType.outbound_dead, note='Queued outbound message failed dispatch with non-retryable error', payload={'message_id': message.id, 'error': message.failure_reason, 'retry_count': message.retry_count, 'route': route_context})
        return message
    _mark_retry(message, reason, failure_code=route_failure_code if isinstance(route_failure_code, str) else None)
    event_type = EventType.outbound_dead if message.status == MessageStatus.dead else EventType.outbound_retry_scheduled
    log_event(db, ticket_id=message.ticket_id, actor_id=message.created_by, event_type=event_type, note='Queued outbound message failed dispatch', payload={'message_id': message.id, 'error': message.failure_reason, 'retry_count': message.retry_count, 'route': route_context})
    return message


def process_outbound_message(db: Session, message: TicketOutboundMessage) -> TicketOutboundMessage:
    if message.status == MessageStatus.sent:
        return message
    if not is_external_outbound_message(message):
        _mark_dead(message, 'Non-external outbound row is not eligible for provider dispatch', failure_code='non_external_outbound_not_dispatchable')
        log_event(db, ticket_id=message.ticket_id, actor_id=message.created_by, event_type=EventType.outbound_dead, note='Non-external outbound row was blocked from provider dispatch', payload={'message_id': message.id, 'channel': message.channel.value if hasattr(message.channel, 'value') else str(message.channel), 'provider_status': message.provider_status})
        return message
    try:
        ticket_for_origin = message.ticket
        _enforce_customer_visible_origin(
            body=message.body,
            origin=_normalize_customer_visible_origin(message.origin, created_by=message.created_by),
            ticket=ticket_for_origin,
            created_by=message.created_by,
            runtime_trace_id=message.runtime_trace_id,
            runtime_contract_version=message.runtime_contract_version,
            runtime_signature=message.runtime_signature,
            safety_status=message.safety_status,
        )
    except ValueError as exc:
        reason = str(exc)
        _mark_origin_blocked(message, reason)
        log_event(db, ticket_id=message.ticket_id, actor_id=message.created_by, event_type=EventType.outbound_dead, note='Customer-visible outbound origin contract blocked dispatch', payload={'message_id': message.id, 'origin': message.origin, 'failure_code': reason, 'required_contract': AI_REPLY_CONTRACT_V2})
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

    if message.channel == SourceChannel.whatsapp:
        status_value, provider_status, sent_at, route_context = _dispatch_whatsapp_message(db, message, ticket, idempotency_key)
        return _handle_dispatch_result(db, message=message, ticket=ticket, status_value=status_value, provider_status=provider_status, sent_at=sent_at, route_context=route_context)

    if message.channel == SourceChannel.email:
        status_value, provider_status, sent_at, route_context = _dispatch_email_message(db, message, ticket, idempotency_key)
        return _handle_dispatch_result(db, message=message, ticket=ticket, status_value=status_value, provider_status=provider_status, sent_at=sent_at, route_context=route_context)

    target = None
    session_key = None
    link = None
    if ticket is not None:
        target = ticket.source_chat_id or ticket.preferred_reply_contact or (ticket.customer.phone if ticket.customer else None)
        if ticket.external_channel_link is not None:
            link = ticket.external_channel_link
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
        'adapter': 'unsupported_external_channel',
    }
    route_context.update({
        'failure_code': 'unsupported_external_channel',
        'error': f'No native dispatcher is configured for channel {channel_value}',
        'retryable': False,
    })
    status_value, provider_status, sent_at = MessageStatus.failed, 'unsupported_external_channel', None
    return _handle_dispatch_result(db, message=message, ticket=ticket, status_value=status_value, provider_status=provider_status, sent_at=sent_at, route_context=route_context)


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
