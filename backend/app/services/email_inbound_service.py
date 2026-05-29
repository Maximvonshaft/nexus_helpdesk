from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..enums import ConversationState, EventType, SourceChannel, TicketStatus
from ..models import Customer, Ticket, TicketInboundEmailMessage, TicketOutboundMessage, User
from ..schemas import InboundEmailIngestRequest
from ..utils.normalize import normalize_email
from ..utils.time import ensure_utc, utc_now
from .audit_service import log_admin_audit, log_event
from .email_mailbox_identity import (
    build_inbound_mailbox_message_id,
    build_mailbox_thread_id,
    normalize_mailbox_header_id,
    normalize_mailbox_references,
)
from .permissions import ensure_can_manage_runtime, ensure_ticket_visible

MAX_CUSTOMER_MESSAGE_PREVIEW = 4000
MAX_AUDIT_BODY_PREVIEW = 500


@dataclass(frozen=True)
class InboundEmailIngestResult:
    row: TicketInboundEmailMessage
    created: bool


def _clip(value: str | None, limit: int) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text[:limit]


def _safe_address(value: str | None) -> str | None:
    text = _clip(value, 320)
    if not text or "\r" in text or "\n" in text:
        return None
    return text


def _valid_from_address(value: str | None) -> str:
    normalized = normalize_email(value)
    if not normalized or "@" not in normalized or "\r" in normalized or "\n" in normalized:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_inbound_email_from_address")
    return normalized[:320]


def _known_mailbox_rows(db: Session, ticket_id: int) -> list[TicketOutboundMessage]:
    return (
        db.query(TicketOutboundMessage)
        .filter(TicketOutboundMessage.ticket_id == ticket_id)
        .filter(TicketOutboundMessage.mailbox_thread_id.isnot(None))
        .order_by(TicketOutboundMessage.created_at.desc(), TicketOutboundMessage.id.desc())
        .limit(50)
        .all()
    )


def _text_contains_any(text: str, values: Iterable[str | None]) -> bool:
    haystack = text or ""
    return any(value and value in haystack for value in values)


def _resolve_mailbox_thread_id(db: Session, ticket: Ticket, payload: InboundEmailIngestRequest) -> str:
    provided_thread = normalize_mailbox_header_id(payload.mailbox_thread_id)
    provided_message = normalize_mailbox_header_id(payload.mailbox_message_id)
    provided_reply_to = normalize_mailbox_header_id(payload.in_reply_to)
    references = normalize_mailbox_references(payload.mailbox_references)
    known_rows = _known_mailbox_rows(db, ticket.id)
    known_thread = next((row.mailbox_thread_id for row in known_rows if row.mailbox_thread_id), None)
    known_text = " ".join(item for item in [provided_thread, provided_message, provided_reply_to, references] if item)

    for row in known_rows:
        if _text_contains_any(known_text, [row.mailbox_thread_id, row.mailbox_message_id, row.provider_message_id]):
            return str(row.mailbox_thread_id)

    return provided_thread or known_thread or build_mailbox_thread_id(ticket.id)


def _resolve_mailbox_references(thread_id: str, payload: InboundEmailIngestRequest) -> str:
    references = normalize_mailbox_references(" ".join(item for item in [payload.mailbox_references, payload.in_reply_to, thread_id] if item))
    return references or thread_id


def _existing_inbound(
    db: Session,
    *,
    ticket_id: int,
    provider: str,
    provider_message_id: str | None,
    mailbox_message_id: str | None,
) -> TicketInboundEmailMessage | None:
    if provider_message_id:
        row = (
            db.query(TicketInboundEmailMessage)
            .filter(TicketInboundEmailMessage.provider == provider, TicketInboundEmailMessage.provider_message_id == provider_message_id)
            .first()
        )
        if row is not None:
            return row
    if mailbox_message_id:
        return (
            db.query(TicketInboundEmailMessage)
            .filter(TicketInboundEmailMessage.ticket_id == ticket_id, TicketInboundEmailMessage.mailbox_message_id == mailbox_message_id)
            .first()
        )
    return None


def _update_ticket_from_inbound(ticket: Ticket, *, from_address: str, body: str) -> None:
    now = utc_now()
    ticket.last_customer_message = _clip(body, MAX_CUSTOMER_MESSAGE_PREVIEW)
    if not ticket.customer_request:
        ticket.customer_request = _clip(body, MAX_CUSTOMER_MESSAGE_PREVIEW)
    ticket.preferred_reply_channel = SourceChannel.email.value
    ticket.preferred_reply_contact = from_address
    if not ticket.source_chat_id:
        ticket.source_chat_id = from_address[:120]
    ticket.updated_at = now

    if ticket.status in {TicketStatus.resolved, TicketStatus.closed, TicketStatus.canceled}:
        ticket.status = TicketStatus.pending_assignment
        ticket.reopen_count += 1
        ticket.closed_at = None
        ticket.resolved_at = None
        ticket.conversation_state = ConversationState.reopened_by_customer
    elif ticket.conversation_state != ConversationState.human_review_required:
        ticket.conversation_state = ConversationState.human_owned


def _update_customer_email(db: Session, ticket: Ticket, from_address: str) -> None:
    if ticket.customer_id is None:
        return
    customer = db.query(Customer).filter(Customer.id == ticket.customer_id).first()
    if customer is None:
        return
    if not customer.email:
        customer.email = from_address
        customer.email_normalized = normalize_email(from_address)


def ingest_ticket_inbound_email(
    db: Session,
    *,
    ticket_id: int,
    payload: InboundEmailIngestRequest,
    current_user: User,
) -> InboundEmailIngestResult:
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    ensure_ticket_visible(current_user, ticket, db)
    ensure_can_manage_runtime(current_user, db)

    from_address = _valid_from_address(payload.from_address)
    provider = _clip(payload.provider, 80) or "manual"
    mailbox_message_id = normalize_mailbox_header_id(payload.mailbox_message_id)
    existing = _existing_inbound(
        db,
        ticket_id=ticket.id,
        provider=provider,
        provider_message_id=_clip(payload.provider_message_id, 255),
        mailbox_message_id=mailbox_message_id,
    )
    if existing is not None:
        return InboundEmailIngestResult(row=existing, created=False)

    body = payload.body.strip()
    if not body:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="inbound_email_body_required")

    mailbox_thread_id = _resolve_mailbox_thread_id(db, ticket, payload)
    mailbox_references = _resolve_mailbox_references(mailbox_thread_id, payload)
    received_at = ensure_utc(payload.received_at) or utc_now()
    body_preview = _clip(body, MAX_AUDIT_BODY_PREVIEW)

    row = TicketInboundEmailMessage(
        ticket_id=ticket.id,
        actor_id=current_user.id,
        source="manual_sync",
        provider=provider,
        provider_message_id=_clip(payload.provider_message_id, 255),
        from_address=from_address,
        from_name=_clip(payload.from_name, 160),
        to_address=_safe_address(payload.to_address),
        cc=_clip(payload.cc, 2000),
        subject=_clip(payload.subject, 255),
        body=body,
        body_preview=body_preview,
        mailbox_thread_id=mailbox_thread_id,
        mailbox_message_id=mailbox_message_id,
        mailbox_references=mailbox_references,
        in_reply_to=normalize_mailbox_header_id(payload.in_reply_to),
        received_at=received_at,
    )
    db.add(row)
    db.flush()

    if row.mailbox_message_id is None:
        row.mailbox_message_id = build_inbound_mailbox_message_id(ticket.id, row.id)
        row.mailbox_references = normalize_mailbox_references(f"{row.mailbox_references or ''} {row.mailbox_message_id}") or row.mailbox_references
        db.flush()

    _update_ticket_from_inbound(ticket, from_address=from_address, body=body)
    _update_customer_email(db, ticket, from_address)

    event = log_event(
        db,
        ticket_id=ticket.id,
        actor_id=current_user.id,
        event_type=EventType.comment_added,
        field_name="email.inbound",
        note="Inbound Email received",
        payload={
            "source": row.source,
            "provider": row.provider,
            "provider_message_id": row.provider_message_id,
            "from_address": row.from_address,
            "subject": row.subject,
            "body_preview": row.body_preview,
            "mailbox_thread_id": row.mailbox_thread_id,
            "mailbox_message_id": row.mailbox_message_id,
            "mailbox_references": row.mailbox_references,
            "in_reply_to": row.in_reply_to,
            "received_at": row.received_at.isoformat() if row.received_at else None,
        },
    )
    audit = log_admin_audit(
        db,
        actor_id=current_user.id,
        action="email.inbound.ingested",
        target_type="ticket_inbound_email_message",
        target_id=row.id,
        old_value=None,
        new_value={
            "ticket_id": ticket.id,
            "provider": row.provider,
            "provider_message_id": row.provider_message_id,
            "from_address": row.from_address,
            "subject": row.subject,
            "body_preview": row.body_preview,
            "mailbox_thread_id": row.mailbox_thread_id,
            "mailbox_message_id": row.mailbox_message_id,
        },
    )
    row.ticket_event_id = event.id
    row.audit_id = audit.id
    db.flush()
    return InboundEmailIngestResult(row=row, created=True)
