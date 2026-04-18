from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from ..models import Customer, Ticket
from ..multi_tenant_models import Tenant, TicketTenantLink
from ..schemas import LiteCaseCreate
from ..services.lite_service import _channel, _priority, _find_open_case, serialize_lite_case, serialize_lite_list
from ..services.tenant_ticket_service import ensure_ticket_visible_in_tenant, list_tenant_tickets
from ..services.ticket_service import create_ticket
from ..schemas import CustomerInput, TicketCreate


def _find_open_case_in_tenant(db: Session, tenant_id: int, payload: LiteCaseCreate) -> Optional[Ticket]:
    base = _find_open_case(db, payload)
    if base is None:
        return None
    link = db.query(TicketTenantLink).filter(TicketTenantLink.ticket_id == base.id, TicketTenantLink.tenant_id == tenant_id).first()
    return base if link is not None else None


def list_tenant_lite_cases(db: Session, current_user, current_tenant: Tenant, q: Optional[str] = None, status: Optional[str] = None):
    status_value = None
    status_in = None
    if status:
        from ..enums import TicketStatus
        if status == 'pending_human':
            status_in = [TicketStatus.new.value, TicketStatus.pending_assignment.value, TicketStatus.waiting_internal.value, TicketStatus.escalated.value]
        elif status == 'closed':
            status_in = [s.value for s in TicketStatus if s not in {TicketStatus.new, TicketStatus.pending_assignment, TicketStatus.waiting_internal, TicketStatus.escalated, TicketStatus.in_progress, TicketStatus.waiting_customer, TicketStatus.resolved}]
        else:
            mapping = {
                'new': TicketStatus.new,
                'in_progress': TicketStatus.in_progress,
                'waiting_customer': TicketStatus.waiting_customer,
                'resolved': TicketStatus.resolved,
            }
            internal_status = mapping.get(status)
            if internal_status:
                status_value = internal_status.value
    tickets = list_tenant_tickets(db, current_user, current_tenant, q=q, status_value=status_value, status_in=status_in, limit=100)
    return [serialize_lite_list(t) for t in tickets]


def get_tenant_lite_case(db: Session, ticket_id: int, current_user, current_tenant: Tenant):
    ticket = ensure_ticket_visible_in_tenant(db, current_user, current_tenant, ticket_id)
    return serialize_lite_case(ticket)


def create_tenant_lite_case(db: Session, payload: LiteCaseCreate, current_user, current_tenant: Tenant):
    from .tenant_service import attach_customer_to_tenant, attach_team_to_tenant, attach_ticket_to_tenant

    if payload.upsert_open_case:
        existing = _find_open_case_in_tenant(db, current_tenant.id, payload)
        if existing:
            changed = False
            for field, value in {
                'last_customer_message': payload.last_customer_message,
                'customer_request': payload.customer_request,
                'required_action': payload.required_action,
                'missing_fields': payload.missing_fields,
                'customer_update': payload.customer_update,
            }.items():
                if value is not None and getattr(existing, field) != value:
                    setattr(existing, field, value)
                    changed = True
            if payload.issue_summary and existing.issue_summary != payload.issue_summary:
                existing.issue_summary = payload.issue_summary
                existing.title = payload.issue_summary[:255]
                changed = True
            if changed:
                from ..utils.time import utc_now
                existing.updated_at = utc_now()
                db.flush()
            return serialize_lite_case(existing), 'updated'

    customer = None
    if payload.customer_name or payload.customer_contact:
        customer = CustomerInput(
            name=payload.customer_name or 'Unknown Customer',
            phone=payload.customer_contact if payload.customer_contact and payload.customer_contact.startswith('+') else None,
            email=payload.customer_contact if payload.customer_contact and '@' in payload.customer_contact else None,
        )

    ticket = create_ticket(
        db,
        TicketCreate(
            title=payload.issue_summary[:255],
            description=payload.customer_request,
            source='ai_intake' if payload.ai_summary else 'manual',
            source_channel=_channel(payload.channel),
            priority=_priority(payload.priority),
            category=payload.case_type,
            sub_category=None,
            tracking_number=payload.tracking_number,
            team_id=payload.team_id or current_user.team_id,
            assignee_id=payload.assigned_to,
            customer=customer,
            ai_summary=payload.ai_summary,
            ai_classification=payload.ai_case_type or payload.case_type,
            ai_confidence=None,
            case_type=payload.case_type,
            issue_summary=payload.issue_summary,
            customer_request=payload.customer_request,
            source_chat_id=payload.source_chat_id,
            required_action=payload.required_action or payload.ai_suggested_required_action,
            missing_fields=payload.missing_fields or payload.ai_missing_fields,
            last_customer_message=payload.last_customer_message or payload.customer_request,
            customer_update=payload.customer_update,
            resolution_summary=payload.resolution_summary,
            last_human_update=None,
            requested_time=payload.requested_time,
            destination=payload.destination,
            preferred_reply_channel=payload.preferred_reply_channel,
            preferred_reply_contact=payload.preferred_reply_contact,
            market_id=payload.market_id,
            country_code=payload.country_code,
        ),
        current_user,
    )
    attach_ticket_to_tenant(db, ticket_id=ticket.id, tenant_id=current_tenant.id)
    if ticket.customer_id:
        attach_customer_to_tenant(db, customer_id=ticket.customer_id, tenant_id=current_tenant.id)
    if ticket.team_id:
        attach_team_to_tenant(db, team_id=ticket.team_id, tenant_id=current_tenant.id)
    db.flush()
    return serialize_lite_case(ticket), 'created'
