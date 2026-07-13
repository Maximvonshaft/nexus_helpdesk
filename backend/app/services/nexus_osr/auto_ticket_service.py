from __future__ import annotations

import json
import re
import secrets
import sqlite3
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, object_session

from ...enums import (
    ConversationState,
    EventType,
    SourceChannel,
    TicketPriority,
    TicketSource,
    TicketStatus,
)
from ...models import Customer, Ticket
from ...utils.time import utc_now
from ...webchat_models import WebchatConversation
from ..ticket_event_writer import TicketEventClass, TicketEventWriter
from .case_context import CaseContext, CaseContextStatus
from .persistence import close_case_context, save_case_context


@dataclass(frozen=True)
class AutoTicketResult:
    ticket: Ticket
    created: bool
    case_context: CaseContext
    # This value is data only. It is not an outbound send authorization; any
    # customer-visible delivery must still use the governed message boundary.
    customer_visible_summary: str


class AutoTicketIdentityConflictError(RuntimeError):
    """Conversation and Case Context identify different tickets/cases."""


class AutoTicketReferencedTicketNotFoundError(LookupError):
    """A supplied durable ticket reference no longer resolves."""


MAX_TICKET_NO_GENERATION_ATTEMPTS = 5
POSTGRES_UNIQUE_VIOLATION_SQLSTATE = "23505"
_TICKET_NO_CONSTRAINT_NAMES = {
    "ix_tickets_ticket_no",
    "tickets_ticket_no_key",
    "uq_tickets_ticket_no",
    "ux_tickets_ticket_no",
}
_TERMINAL_TICKET_STATUSES = {
    TicketStatus.resolved,
    TicketStatus.closed,
    TicketStatus.canceled,
}
_TERMINAL_CONTEXT_STATUSES = {
    CaseContextStatus.CLOSED,
    CaseContextStatus.ARCHIVED,
}
_REUSED_TICKET_ACTION = "Review reused Nexus OSR support ticket and follow up with the customer."


def create_or_reuse_ticket_from_case_context(
    db: Session,
    *,
    case_context: CaseContext,
    customer: Customer | None = None,
    conversation: WebchatConversation | None = None,
    source_channel: SourceChannel = SourceChannel.web_chat,
    title: str | None = None,
    description: str | None = None,
    priority: TicketPriority = TicketPriority.medium,
    issue_type: str | None = None,
) -> AutoTicketResult:
    """Create or safely reuse one human-review ticket.

    The caller owns the outer transaction. This service uses a savepoint for the
    complete Customer/Ticket/Conversation/CaseContext/TicketEvent unit. When a
    persisted WebChat conversation exists, it is refreshed under ``FOR UPDATE``
    before ticket resolution so concurrent callers cannot create two tickets for
    the same conversation.
    """

    if case_context.status in _TERMINAL_CONTEXT_STATUSES:
        raise AutoTicketIdentityConflictError("auto_ticket_terminal_case_context")

    with db.begin_nested():
        locked_conversation = _lock_conversation(
            db,
            case_context=case_context,
            conversation=conversation,
        )
        tenant_id = _tenant_id(locked_conversation)
        existing = _find_existing_ticket(
            db,
            case_context=case_context,
            conversation=locked_conversation,
        )

        if existing is not None:
            changed_fields = _project_existing_ticket_to_human_review(
                existing,
                priority=priority,
            )
            if locked_conversation is not None:
                if locked_conversation.ticket_id != existing.id:
                    locked_conversation.ticket_id = existing.id
                    changed_fields.add("conversation.ticket_id")
                locked_conversation.updated_at = utc_now()

            next_context = _persist_ticket_context_transition(
                db,
                case_context=case_context,
                ticket_id=existing.id,
                conversation=locked_conversation,
                tenant_id=tenant_id,
            )
            if changed_fields:
                _write_ticket_event(
                    db,
                    ticket=existing,
                    case_context=next_context,
                    created=False,
                    changed_fields=changed_fields,
                )
            db.flush()
            result = AutoTicketResult(
                ticket=existing,
                created=False,
                case_context=next_context,
                customer_visible_summary=f"Your existing support ticket is {existing.ticket_no}.",
            )
        else:
            resolved_customer = customer or _customer_from_conversation(db, locked_conversation)
            if resolved_customer is None:
                resolved_customer = Customer(
                    name="WebChat Visitor",
                    external_ref=_external_ref(case_context, locked_conversation),
                )
            if resolved_customer.id is None:
                db.add(resolved_customer)
                db.flush()

            ticket = _create_ticket_with_retry(
                db,
                case_context=case_context,
                customer=resolved_customer,
                source_channel=source_channel,
                title=title,
                description=description,
                priority=priority,
                issue_type=issue_type,
            )
            if locked_conversation is not None:
                locked_conversation.ticket_id = ticket.id
                locked_conversation.updated_at = utc_now()

            next_context = _persist_ticket_context_transition(
                db,
                case_context=case_context,
                ticket_id=ticket.id,
                conversation=locked_conversation,
                tenant_id=tenant_id,
            )
            _write_ticket_event(db, ticket=ticket, case_context=next_context, created=True)
            db.flush()
            result = AutoTicketResult(
                ticket=ticket,
                created=True,
                case_context=next_context,
                customer_visible_summary=f"A support ticket has been created. Ticket number: {ticket.ticket_no}.",
            )

    return result


def _lock_conversation(
    db: Session,
    *,
    case_context: CaseContext,
    conversation: WebchatConversation | None,
) -> WebchatConversation | None:
    context_conversation_id = _optional_identity(
        case_context.conversation_id,
        field="conversation_id",
    )
    if conversation is None:
        return None

    conversation_id = _optional_identity(conversation.id, field="conversation_id")
    if conversation_id is None:
        if context_conversation_id is not None:
            raise AutoTicketIdentityConflictError("auto_ticket_unpersisted_conversation_conflict")
        return conversation
    if context_conversation_id is not None and context_conversation_id != conversation_id:
        raise AutoTicketIdentityConflictError("auto_ticket_conversation_identity_conflict")

    locked = (
        db.query(WebchatConversation)
        .filter(WebchatConversation.id == conversation_id)
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if locked is None:
        raise AutoTicketIdentityConflictError("auto_ticket_conversation_not_found")
    return locked


def _find_existing_ticket(
    db: Session,
    *,
    case_context: CaseContext,
    conversation: WebchatConversation | None,
) -> Ticket | None:
    context_ticket_id = _optional_identity(case_context.ticket_id, field="ticket_id")
    conversation_ticket_id = _optional_identity(
        getattr(conversation, "ticket_id", None),
        field="ticket_id",
    )
    if (
        context_ticket_id is not None
        and conversation_ticket_id is not None
        and context_ticket_id != conversation_ticket_id
    ):
        raise AutoTicketIdentityConflictError("auto_ticket_ticket_identity_conflict")

    ticket_id = context_ticket_id or conversation_ticket_id
    if ticket_id is None:
        return None
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise AutoTicketReferencedTicketNotFoundError("auto_ticket_referenced_ticket_not_found")
    return ticket


def _persist_ticket_context_transition(
    db: Session,
    *,
    case_context: CaseContext,
    ticket_id: int,
    conversation: WebchatConversation | None,
    tenant_id: str,
) -> CaseContext:
    original_conversation_id = _optional_identity(
        case_context.conversation_id,
        field="conversation_id",
    )
    original_ticket_id = _optional_identity(case_context.ticket_id, field="ticket_id")
    target_conversation_id = (
        _optional_identity(conversation.id, field="conversation_id")
        if conversation is not None
        else original_conversation_id
    )
    if (
        original_conversation_id is not None
        and target_conversation_id is not None
        and original_conversation_id != target_conversation_id
    ):
        raise AutoTicketIdentityConflictError("auto_ticket_context_transition_conflict")
    if original_ticket_id is not None and original_ticket_id != ticket_id:
        raise AutoTicketIdentityConflictError("auto_ticket_context_ticket_transition_conflict")

    target_context = replace(
        case_context,
        conversation_id=target_conversation_id,
    ).mark_ticket_created(ticket_id)
    original_identity = (original_conversation_id, original_ticket_id)
    target_identity = (target_conversation_id, int(ticket_id))

    if original_identity != target_identity and any(item is not None for item in original_identity):
        close_case_context(
            db,
            conversation_id=original_conversation_id,
            ticket_id=original_ticket_id,
            tenant_id=tenant_id,
        )
    save_case_context(db, target_context, tenant_id=tenant_id)
    return target_context


def _create_ticket_with_retry(
    db: Session,
    *,
    case_context: CaseContext,
    customer: Customer,
    source_channel: SourceChannel,
    title: str | None,
    description: str | None,
    priority: TicketPriority,
    issue_type: str | None,
) -> Ticket:
    last_error: IntegrityError | None = None
    for attempt in range(MAX_TICKET_NO_GENERATION_ATTEMPTS):
        ticket = _build_ticket(
            case_context=case_context,
            customer=customer,
            source_channel=source_channel,
            title=title,
            description=description,
            priority=priority,
            issue_type=issue_type,
            ticket_no=_generate_ticket_no(case_context, attempt=attempt),
        )
        try:
            with db.begin_nested():
                db.add(ticket)
                db.flush()
            return ticket
        except IntegrityError as exc:
            _discard_failed_ticket(db, ticket)
            if not _is_ticket_no_unique_violation(exc):
                raise
            last_error = exc
            db.expire_all()
            _ticket_no_exists(db, ticket.ticket_no)

    if last_error is not None:
        raise last_error
    raise RuntimeError("unable_to_create_nexus_osr_auto_ticket")


def _build_ticket(
    *,
    case_context: CaseContext,
    customer: Customer,
    source_channel: SourceChannel,
    title: str | None,
    description: str | None,
    priority: TicketPriority,
    issue_type: str | None,
    ticket_no: str,
) -> Ticket:
    now = utc_now()
    return Ticket(
        ticket_no=ticket_no,
        title=title or _default_title(case_context),
        description=description or _default_description(case_context),
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=source_channel,
        priority=priority,
        status=TicketStatus.pending_assignment,
        conversation_state=ConversationState.human_review_required,
        preferred_reply_channel=_source_channel_value(source_channel),
        preferred_reply_contact=_preferred_contact(case_context),
        country_code=case_context.country_code,
        case_type=issue_type or case_context.issue_type,
        customer_request=case_context.customer_claim_summary,
        required_action="Review OSR-created support ticket and follow up with the customer.",
        missing_fields=", ".join(case_context.missing_info) if case_context.missing_info else None,
        tracking_number=None,
        created_at=now,
        updated_at=now,
    )


def _project_existing_ticket_to_human_review(
    ticket: Ticket,
    *,
    priority: TicketPriority,
) -> set[str]:
    """Project a reused ticket without replacing ownership or business facts."""

    changed_fields: set[str] = set()
    terminal_status = ticket.status in _TERMINAL_TICKET_STATUSES
    if terminal_status:
        target_status = (
            TicketStatus.in_progress
            if ticket.assignee_id is not None
            else TicketStatus.pending_assignment
        )
        if ticket.status != target_status:
            ticket.status = target_status
            changed_fields.add("status")
        if ticket.closed_at is not None:
            ticket.closed_at = None
            changed_fields.add("closed_at")
        if ticket.resolved_at is not None:
            ticket.resolved_at = None
            changed_fields.add("resolved_at")
        ticket.reopen_count = int(ticket.reopen_count or 0) + 1
        changed_fields.add("reopen_count")
    elif ticket.assignee_id is not None and ticket.status in {
        TicketStatus.new,
        TicketStatus.pending_assignment,
    }:
        ticket.status = TicketStatus.in_progress
        changed_fields.add("status")
    elif ticket.assignee_id is None and ticket.status == TicketStatus.new:
        ticket.status = TicketStatus.pending_assignment
        changed_fields.add("status")

    target_conversation_state = (
        ConversationState.human_owned
        if ticket.assignee_id is not None
        else ConversationState.human_review_required
    )
    if ticket.conversation_state != target_conversation_state:
        ticket.conversation_state = target_conversation_state
        changed_fields.add("conversation_state")

    next_priority = _max_priority(ticket.priority, priority)
    if ticket.priority != next_priority:
        ticket.priority = next_priority
        changed_fields.add("priority")

    if not ticket.required_action:
        ticket.required_action = _REUSED_TICKET_ACTION
        changed_fields.add("required_action")

    if changed_fields:
        ticket.updated_at = utc_now()
    return changed_fields


def _customer_from_conversation(
    db: Session,
    conversation: WebchatConversation | None,
) -> Customer | None:
    if conversation is None or not getattr(conversation, "ticket_id", None):
        return None
    ticket = db.get(Ticket, conversation.ticket_id)
    return db.get(Customer, ticket.customer_id) if ticket and ticket.customer_id else None


def _external_ref(
    case_context: CaseContext,
    conversation: WebchatConversation | None,
) -> str:
    if conversation is not None:
        return f"webchat:{conversation.public_id}"
    if case_context.conversation_id is not None:
        return f"conversation:{case_context.conversation_id}"
    return "osr:auto-ticket"


def _generate_ticket_no(case_context: CaseContext, *, attempt: int = 0) -> str:
    del attempt  # every retry receives a fresh cryptographic suffix
    prefix = re.sub(
        r"[^A-Z0-9]",
        "",
        (case_context.country_code or "OSR").upper(),
    )[:8] or "OSR"
    timestamp = utc_now().strftime("%Y%m%d%H%M%S")
    suffix = secrets.token_hex(5).upper()
    ticket_no = f"OSR-{prefix}-{timestamp}-{suffix}"
    if len(ticket_no) > 40:
        raise RuntimeError("generated_osr_ticket_number_exceeds_contract")
    return ticket_no


def _ticket_no_exists(db: Session, ticket_no: str) -> bool:
    with db.no_autoflush:
        return db.query(Ticket.id).filter(Ticket.ticket_no == ticket_no).first() is not None


def _is_ticket_no_unique_violation(exc: IntegrityError) -> bool:
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    constraint_name = getattr(getattr(orig, "diag", None), "constraint_name", None)
    normalized_constraint = str(constraint_name or "").lower()

    if sqlstate == POSTGRES_UNIQUE_VIOLATION_SQLSTATE:
        if normalized_constraint:
            return (
                normalized_constraint in _TICKET_NO_CONSTRAINT_NAMES
                or "ticket_no" in normalized_constraint
            )
        return _statement_targets_ticket_no(exc)

    sqlite_errorcode = getattr(orig, "sqlite_errorcode", None)
    if sqlite_errorcode in {
        sqlite3.SQLITE_CONSTRAINT,
        sqlite3.SQLITE_CONSTRAINT_UNIQUE,
    }:
        message = str(orig or "").lower()
        return "tickets.ticket_no" in message or _statement_targets_ticket_no(exc)

    message = str(orig or exc).lower()
    return "ticket_no" in message and ("unique" in message or "duplicate" in message)


def _statement_targets_ticket_no(exc: IntegrityError) -> bool:
    statement = str(getattr(exc, "statement", None) or "").lower()
    params = getattr(exc, "params", None)
    params_include_ticket_no = isinstance(params, dict) and "ticket_no" in params
    return (
        "tickets" in statement
        and "ticket_no" in statement
        and (params_include_ticket_no or "insert" in statement)
    )


def _discard_failed_ticket(db: Session, ticket: Ticket) -> None:
    if object_session(ticket) is db:
        db.expunge(ticket)


def _optional_identity(value: Any, *, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise AutoTicketIdentityConflictError(f"auto_ticket_invalid_{field}")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise AutoTicketIdentityConflictError(f"auto_ticket_invalid_{field}") from exc
    if parsed <= 0:
        raise AutoTicketIdentityConflictError(f"auto_ticket_invalid_{field}")
    return parsed


def _tenant_id(conversation: WebchatConversation | None) -> str:
    return (str(getattr(conversation, "tenant_key", None) or "default").strip() or "default")[:80]


def _max_priority(
    left: TicketPriority | None,
    right: TicketPriority,
) -> TicketPriority:
    order = {
        TicketPriority.low: 0,
        TicketPriority.medium: 1,
        TicketPriority.high: 2,
        TicketPriority.urgent: 3,
    }
    if left is None:
        return right
    return right if order.get(right, 0) > order.get(left, 0) else left


def _default_title(case_context: CaseContext) -> str:
    issue = case_context.issue_type or "customer support"
    ref = (
        f" - {case_context.safe_tracking_reference}"
        if case_context.safe_tracking_reference
        else ""
    )
    return f"OSR {issue}{ref}"[:200]


def _default_description(case_context: CaseContext) -> str:
    parts = [
        "Auto-created by Nexus OSR.",
        f"Issue type: {case_context.issue_type or 'unknown'}.",
    ]
    if case_context.safe_tracking_reference:
        parts.append(f"Tracking reference: {case_context.safe_tracking_reference}.")
    if case_context.customer_claim_summary:
        parts.append(f"Customer request: {case_context.customer_claim_summary}")
    if case_context.missing_info:
        parts.append(f"Missing info: {', '.join(case_context.missing_info)}.")
    return "\n".join(parts)


def _source_channel_value(channel: SourceChannel) -> str:
    return channel.value if hasattr(channel, "value") else str(channel)


def _preferred_contact(case_context: CaseContext) -> str | None:
    if not case_context.contact_methods:
        return None
    first = case_context.contact_methods[0]
    return f"{first.channel}:{first.value_redacted}"


def _write_ticket_event(
    db: Session,
    *,
    ticket: Ticket,
    case_context: CaseContext,
    created: bool,
    changed_fields: set[str] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "source": "nexus_osr",
        "created": created,
        "operator_projection": _enum_value(ticket.conversation_state),
        "ticket_status": _enum_value(ticket.status),
        "conversation_state": _enum_value(ticket.conversation_state),
        "case_context_state": {
            "status": _enum_value(case_context.status),
            "ticket_created": bool(case_context.ticket_created),
            "handoff_requested": bool(case_context.handoff_requested),
            "has_tracking_reference": bool(
                case_context.safe_tracking_reference
                or case_context.tracking_number_hash
            ),
            "has_contact_method": bool(case_context.contact_methods),
            "missing_info_count": len(case_context.missing_info),
        },
    }
    if changed_fields:
        payload["changed_fields"] = sorted(changed_fields)
    TicketEventWriter.add(
        db,
        ticket_id=ticket.id,
        actor_id=None,
        event_type=EventType.field_updated,
        event_class=TicketEventClass.INTERNAL_AUDIT,
        note="Nexus OSR auto ticket created" if created else "Nexus OSR ticket reused",
        payload=payload,
    )


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value
