from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ...enums import ConversationState, EventType, SourceChannel, TicketPriority, TicketSource, TicketStatus
from ...models import Customer, Ticket, TicketEvent
from ...utils.time import utc_now
from ...webchat_models import WebchatConversation
from .case_context import CaseContext
from .persistence import save_case_context


@dataclass(frozen=True)
class AutoTicketResult:
    ticket: Ticket
    created: bool
    case_context: CaseContext
    customer_visible_summary: str


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
    """Create or reuse a support ticket from OSR Case Context.

    This is the production-side replacement for the framework-light
    `ticket.create` handler. It is deliberately idempotent by existing ticket id,
    conversation ticket id, and safe tracking hash where available.
    """

    existing = _find_existing_ticket(db, case_context=case_context, conversation=conversation)
    if existing is not None:
        next_context = case_context.mark_ticket_created(existing.id)
        save_case_context(db, next_context, tenant_id=getattr(conversation, "tenant_key", None) or "default")
        return AutoTicketResult(
            ticket=existing,
            created=False,
            case_context=next_context,
            customer_visible_summary=f"Your existing support ticket is {existing.ticket_no}.",
        )

    customer = customer or _customer_from_conversation(db, conversation)
    if customer is None:
        customer = Customer(name="WebChat Visitor", external_ref=_external_ref(case_context, conversation))
        db.add(customer)
        db.flush()

    ticket = Ticket(
        ticket_no=_generate_ticket_no(db, case_context),
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
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db.add(ticket)
    db.flush()
    if conversation is not None:
        conversation.ticket_id = ticket.id
        conversation.updated_at = utc_now()
    next_context = case_context.mark_ticket_created(ticket.id)
    save_case_context(db, next_context, tenant_id=getattr(conversation, "tenant_key", None) or "default")
    _write_ticket_event(db, ticket=ticket, case_context=next_context, created=True)
    db.flush()
    return AutoTicketResult(
        ticket=ticket,
        created=True,
        case_context=next_context,
        customer_visible_summary=f"A support ticket has been created. Ticket number: {ticket.ticket_no}.",
    )


def _find_existing_ticket(db: Session, *, case_context: CaseContext, conversation: WebchatConversation | None) -> Ticket | None:
    if case_context.ticket_id is not None and str(case_context.ticket_id).isdigit():
        row = db.get(Ticket, int(case_context.ticket_id))
        if row is not None:
            return row
    if conversation is not None and getattr(conversation, "ticket_id", None):
        row = db.get(Ticket, conversation.ticket_id)
        if row is not None:
            return row
    if case_context.tracking_number_hash:
        # We do not store raw tracking numbers here. Reuse by CaseContext records
        # is handled before this point, so this fallback is intentionally empty
        # until a dedicated tracking-hash index exists on Ticket.
        return None
    return None


def _customer_from_conversation(db: Session, conversation: WebchatConversation | None) -> Customer | None:
    if conversation is None or not getattr(conversation, "ticket_id", None):
        return None
    ticket = db.get(Ticket, conversation.ticket_id)
    return db.get(Customer, ticket.customer_id) if ticket and ticket.customer_id else None


def _external_ref(case_context: CaseContext, conversation: WebchatConversation | None) -> str:
    if conversation is not None:
        return f"webchat:{conversation.public_id}"
    if case_context.conversation_id is not None:
        return f"conversation:{case_context.conversation_id}"
    return "osr:auto-ticket"


def _generate_ticket_no(db: Session, case_context: CaseContext) -> str:
    prefix = (case_context.country_code or "OSR").upper()[:8]
    count = db.query(Ticket.id).count() + 1
    return f"OSR-{prefix}-{count:06d}"


def _default_title(case_context: CaseContext) -> str:
    issue = case_context.issue_type or "customer support"
    ref = f" - {case_context.safe_tracking_reference}" if case_context.safe_tracking_reference else ""
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


def _write_ticket_event(db: Session, *, ticket: Ticket, case_context: CaseContext, created: bool) -> None:
    payload: dict[str, Any] = {
        "source": "nexus_osr",
        "created": created,
        "case_context": case_context.as_dict(),
    }
    db.add(TicketEvent(
        ticket_id=ticket.id,
        actor_id=None,
        event_type=EventType.field_updated,
        note="Nexus OSR auto ticket created" if created else "Nexus OSR ticket reused",
        payload_json=json.dumps(payload, ensure_ascii=False, default=str),
    ))
