from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import and_, exists, func, or_
from sqlalchemy.orm import Session, joinedload, selectinload

from ..enums import MessageStatus, SourceChannel, TicketStatus, UserRole
from ..models import Customer, Ticket, TicketInboundEmailMessage, TicketOutboundMessage, User
from ..schemas import EmailMailboxQueueItem, EmailMailboxQueueResponse
from ..utils.time import utc_now
from .permissions import (
    CAP_OUTBOUND_DRAFT_SAVE,
    CAP_OUTBOUND_SEND,
    CAP_TICKET_READ,
    ensure_capability,
    resolve_capabilities,
)

EMAIL_QUEUE_TOKENS = {"email", "mail", "smtp", "imap", "pop3"}
TERMINAL_STATUSES = {TicketStatus.closed, TicketStatus.canceled}


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


def _clip(value: str | None, limit: int = 240) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split())
    if not normalized:
        return None
    return normalized[:limit]


def _normalize_query(value: str | None) -> str | None:
    normalized = " ".join(str(value or "").strip().split())
    if not normalized:
        return None
    return normalized[:80]


def _tokenize(value: str | None) -> set[str]:
    if not value:
        return set()
    normalized = str(value).lower().replace("e-mail", "email").replace("e_mail", "email")
    tokens: list[str] = []
    current: list[str] = []
    for char in normalized:
        if char.isalnum():
            current.append(char)
        else:
            if current:
                tokens.append("".join(current))
                current = []
    if current:
        tokens.append("".join(current))
    return set(tokens)


def _has_email_marker(ticket: Ticket) -> bool:
    if ticket.source_channel == SourceChannel.email:
        return True
    values = [ticket.category, ticket.sub_category, ticket.preferred_reply_channel, ticket.source_chat_id]
    return any(_tokenize(value) & EMAIL_QUEUE_TOKENS for value in values)


def _marker_prefilter():
    queue_tokens = sorted(EMAIL_QUEUE_TOKENS)
    values = [
        func.lower(Ticket.category),
        func.lower(Ticket.sub_category),
        func.lower(Ticket.preferred_reply_channel),
        func.lower(Ticket.source_chat_id),
    ]
    token_checks = []
    for column in values:
        token_checks.append(column.in_(queue_tokens))
        token_checks.append(column.like("%email%"))
        token_checks.append(column.like("%e-mail%"))
        token_checks.append(column.like("%smtp%"))
        token_checks.append(column.like("%imap%"))
        token_checks.append(column.like("%pop3%"))
    return or_(Ticket.source_channel == SourceChannel.email, *token_checks)


def _ticket_overdue(ticket: Ticket) -> bool:
    if ticket.resolution_due_at is None:
        return False
    if ticket.status in TERMINAL_STATUSES:
        return False
    return ticket.resolution_due_at < utc_now()


def _latest_inbound(ticket: Ticket) -> TicketInboundEmailMessage | None:
    if not ticket.inbound_email_messages:
        return None
    return max(ticket.inbound_email_messages, key=lambda row: row.received_at or row.created_at)


def _latest_email_outbound(ticket: Ticket) -> TicketOutboundMessage | None:
    rows = [row for row in ticket.outbound_messages if row.channel == SourceChannel.email]
    if not rows:
        return None
    return max(rows, key=lambda row: row.updated_at or row.sent_at or row.created_at)


def _outbound_reason(row: TicketOutboundMessage) -> str:
    if row.status == MessageStatus.dead:
        return "outbound_dead"
    if row.status == MessageStatus.failed:
        return "outbound_failed"
    if row.status in {MessageStatus.pending, MessageStatus.processing}:
        return "outbound_pending"
    if row.status == MessageStatus.draft:
        return "draft_saved"
    return "outbound_sent"


def _reason_rank(reason: str) -> int:
    return {
        "customer_reply_received": 0,
        "outbound_dead": 1,
        "outbound_failed": 2,
        "outbound_pending": 3,
        "draft_saved": 4,
        "email_ticket_marker": 5,
        "outbound_sent": 6,
    }.get(reason, 9)


def _build_queue_item(ticket: Ticket) -> EmailMailboxQueueItem | None:
    inbound = _latest_inbound(ticket)
    outbound = _latest_email_outbound(ticket)
    inbound_at = inbound.received_at if inbound else None
    outbound_at = (outbound.updated_at or outbound.sent_at or outbound.created_at) if outbound else None
    use_inbound = bool(inbound and (not outbound_at or (inbound_at and inbound_at >= outbound_at)))

    if use_inbound and inbound:
        queue_source = "inbound_email"
        queue_reason = "customer_reply_received"
        direction = "inbound"
        last_message_at = inbound.received_at or inbound.created_at
        last_message_subject = inbound.subject
        last_message_preview = inbound.body_preview or inbound.body
        mailbox_thread_id = inbound.mailbox_thread_id
        mailbox_message_id = inbound.mailbox_message_id
        mailbox_references = inbound.mailbox_references
        provider = inbound.provider
        provider_status = "received"
        delivery_status = None
        inbound_message_id = inbound.id
        outbound_message_id = None
    elif outbound:
        queue_source = "outbound_message"
        queue_reason = _outbound_reason(outbound)
        direction = "outbound"
        last_message_at = outbound.updated_at or outbound.sent_at or outbound.created_at
        last_message_subject = outbound.subject
        last_message_preview = outbound.error_message or outbound.failure_reason or outbound.body
        mailbox_thread_id = outbound.mailbox_thread_id
        mailbox_message_id = outbound.mailbox_message_id
        mailbox_references = outbound.mailbox_references
        provider = "smtp"
        provider_status = outbound.provider_status or _enum_value(outbound.status)
        delivery_status = outbound.delivery_status
        inbound_message_id = None
        outbound_message_id = outbound.id
    elif _has_email_marker(ticket):
        queue_source = "ticket_marker"
        queue_reason = "email_ticket_marker"
        direction = "ticket"
        last_message_at = ticket.updated_at
        last_message_subject = ticket.title
        last_message_preview = ticket.last_customer_message or ticket.customer_request or ticket.issue_summary or ticket.description
        mailbox_thread_id = None
        mailbox_message_id = None
        mailbox_references = None
        provider = None
        provider_status = None
        delivery_status = None
        inbound_message_id = None
        outbound_message_id = None
    else:
        return None

    customer = ticket.customer
    return EmailMailboxQueueItem(
        id=ticket.id,
        ticket_id=ticket.id,
        ticket_no=ticket.ticket_no,
        title=ticket.issue_summary or ticket.title,
        status=_enum_value(ticket.status) or "",
        priority=_enum_value(ticket.priority) or "",
        source_channel=_enum_value(ticket.source_channel),
        category=ticket.category,
        sub_category=ticket.sub_category,
        tracking_number=ticket.tracking_number,
        customer_name=customer.name if customer else None,
        customer_email=customer.email if customer else None,
        assignee_name=ticket.assignee.display_name if ticket.assignee else None,
        team_name=ticket.team.name if ticket.team else None,
        market_id=ticket.market_id,
        market_code=ticket.market.code if ticket.market else None,
        country_code=ticket.country_code,
        conversation_state=_enum_value(ticket.conversation_state),
        updated_at=ticket.updated_at,
        resolution_due_at=ticket.resolution_due_at,
        overdue=_ticket_overdue(ticket),
        queue_source=queue_source,
        queue_reason=queue_reason,
        direction=direction,
        last_message_at=last_message_at,
        last_message_subject=_clip(last_message_subject, 255),
        last_message_preview=_clip(last_message_preview, 320),
        mailbox_thread_id=mailbox_thread_id,
        mailbox_message_id=mailbox_message_id,
        mailbox_references=_clip(mailbox_references, 500),
        provider=provider,
        provider_status=provider_status,
        delivery_status=delivery_status,
        outbound_message_id=outbound_message_id,
        inbound_message_id=inbound_message_id,
    )


def _status_filter(value: str | None):
    if not value:
        return None
    try:
        return TicketStatus(value)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Unsupported status") from exc


def build_email_mailbox_queue(
    db: Session,
    current_user: User,
    *,
    q: str | None = None,
    status_value: str | None = None,
    limit: int = 50,
) -> EmailMailboxQueueResponse:
    ensure_capability(current_user, CAP_TICKET_READ, db, message="Ticket not visible for current user")
    capabilities = resolve_capabilities(current_user, db)
    if not ({CAP_OUTBOUND_DRAFT_SAVE, CAP_OUTBOUND_SEND} & capabilities):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="email_queue_requires_outbound_capability")

    normalized_q = _normalize_query(q)
    safe_limit = max(1, min(int(limit or 50), 100))
    requested_status = _status_filter(status_value)

    inbound_exists = exists().where(TicketInboundEmailMessage.ticket_id == Ticket.id)
    outbound_exists = exists().where(
        and_(
            TicketOutboundMessage.ticket_id == Ticket.id,
            TicketOutboundMessage.channel == SourceChannel.email,
        )
    )

    query = db.query(Ticket).options(
        joinedload(Ticket.customer),
        joinedload(Ticket.assignee),
        joinedload(Ticket.team),
        joinedload(Ticket.market),
        selectinload(Ticket.inbound_email_messages),
        selectinload(Ticket.outbound_messages),
    )
    if current_user.role not in {UserRole.admin, UserRole.manager, UserRole.auditor}:
        query = query.filter(or_(Ticket.team_id == current_user.team_id, Ticket.assignee_id == current_user.id))

    query = query.filter(or_(inbound_exists, outbound_exists, _marker_prefilter()))

    if requested_status:
        query = query.filter(Ticket.status == requested_status)

    if normalized_q:
        like = f"%{normalized_q}%"
        inbound_search = exists().where(
            and_(
                TicketInboundEmailMessage.ticket_id == Ticket.id,
                or_(
                    TicketInboundEmailMessage.subject.ilike(like),
                    TicketInboundEmailMessage.from_address.ilike(like),
                    TicketInboundEmailMessage.mailbox_thread_id.ilike(like),
                    TicketInboundEmailMessage.mailbox_message_id.ilike(like),
                    TicketInboundEmailMessage.provider_message_id.ilike(like),
                ),
            )
        )
        outbound_search = exists().where(
            and_(
                TicketOutboundMessage.ticket_id == Ticket.id,
                TicketOutboundMessage.channel == SourceChannel.email,
                or_(
                    TicketOutboundMessage.subject.ilike(like),
                    TicketOutboundMessage.mailbox_thread_id.ilike(like),
                    TicketOutboundMessage.mailbox_message_id.ilike(like),
                    TicketOutboundMessage.provider_message_id.ilike(like),
                    TicketOutboundMessage.provider_status.ilike(like),
                    TicketOutboundMessage.delivery_status.ilike(like),
                ),
            )
        )
        query = query.outerjoin(Customer, Customer.id == Ticket.customer_id).filter(
            or_(
                Ticket.ticket_no.ilike(like),
                Ticket.title.ilike(like),
                Ticket.description.ilike(like),
                Ticket.tracking_number.ilike(like),
                Customer.name.ilike(like),
                Customer.email.ilike(like),
                inbound_search,
                outbound_search,
            )
        )

    tickets = query.order_by(Ticket.updated_at.desc(), Ticket.id.desc()).limit(safe_limit * 3).all()
    items = [item for ticket in tickets if (item := _build_queue_item(ticket)) is not None]
    items = sorted(
        items,
        key=lambda item: (
            _reason_rank(item.queue_reason),
            -(item.last_message_at or item.updated_at).timestamp(),
            -item.ticket_id,
        ),
    )[:safe_limit]
    return EmailMailboxQueueResponse(
        generated_at=utc_now(),
        items=items,
        total=len(items),
        filters={
            "q": normalized_q,
            "status": status_value,
            "limit": safe_limit,
            "source": "mailbox_projection",
        },
    )
