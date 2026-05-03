from __future__ import annotations

import json
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..enums import EventType, ResolutionCategory, SourceChannel, TicketPriority, TicketSource, TicketStatus
from ..models import Customer, Ticket, TicketAIIntake, TicketInternalNote, User
from ..schemas import (
    AIIntakeCreate,
    CustomerInput,
    InternalNoteCreate,
    LiteAIIntakeRequest,
    LiteAssignRequest,
    LiteCaseCreate,
    LiteCaseDetail,
    LiteCaseListItem,
    LiteCaseUpdate,
    LiteHumanNoteRequest,
    LiteStatusRequest,
    LiteWorkflowUpdateRequest,
    TicketCreate,
    TicketStatusChangeRequest,
)
from ..utils.time import utc_now
from .audit_service import log_event
from .permissions import ensure_can_assign, ensure_can_change_status, ensure_ticket_visible
from .ticket_service import add_ai_intake, add_internal_note, change_status, create_ticket, get_ticket_or_404, list_tickets, validate_assignee_team
from .sla_service import evaluate_sla, resume_sla, update_pause_state_for_status
from .state_machine import requires_note, validate_transition

LITE_STATUS_ORDER = [
    "new",
    "pending_human",
    "in_progress",
    "waiting_customer",
    "resolved",
    "closed",
]


def _internal_to_lite(status: TicketStatus) -> str:
    if status == TicketStatus.new:
        return "new"
    if status in {TicketStatus.pending_assignment, TicketStatus.waiting_internal, TicketStatus.escalated}:
        return "pending_human"
    if status == TicketStatus.in_progress:
        return "in_progress"
    if status == TicketStatus.waiting_customer:
        return "waiting_customer"
    if status == TicketStatus.resolved:
        return "resolved"
    return "closed"


def _lite_status_to_internal(ticket: Ticket, lite_status: str) -> TicketStatus:
    if lite_status == "new":
        return TicketStatus.new
    if lite_status == "pending_human":
        if ticket.status in {TicketStatus.new, TicketStatus.pending_assignment}:
            return TicketStatus.pending_assignment
        return TicketStatus.waiting_internal
    if lite_status == "in_progress":
        return TicketStatus.in_progress
    if lite_status == "waiting_customer":
        return TicketStatus.waiting_customer
    if lite_status == "resolved":
        return TicketStatus.resolved
    if lite_status == "closed":
        return TicketStatus.closed
    raise HTTPException(status_code=400, detail="Unsupported status")


def _priority(value: Optional[str]) -> TicketPriority:
    if not value:
        return TicketPriority.medium
    try:
        return TicketPriority(value.lower())
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Unsupported priority") from exc


def _channel(value: Optional[str]) -> SourceChannel:
    if not value:
        return SourceChannel.whatsapp
    try:
        return SourceChannel(value.lower())
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Unsupported channel") from exc


def _contact_value(customer: Optional[Customer]) -> Optional[str]:
    if not customer:
        return None
    return customer.phone or customer.email or customer.external_ref


def _latest_ai(ticket: Ticket) -> Optional[TicketAIIntake]:
    if not ticket.ai_intakes:
        return None
    return sorted(ticket.ai_intakes, key=lambda x: x.created_at, reverse=True)[0]


def serialize_lite_case(ticket: Ticket) -> LiteCaseDetail:
    latest_ai = _latest_ai(ticket)
    return LiteCaseDetail(
        id=ticket.id,
        case=ticket.ticket_no,
        case_type=ticket.case_type or ticket.category or ticket.ai_classification,
        issue_summary=ticket.issue_summary or ticket.title,
        customer_request=ticket.customer_request or ticket.description,
        status=_internal_to_lite(ticket.status),
        priority=ticket.priority.value,
        customer_name=ticket.customer.name if ticket.customer else None,
        customer_contact=_contact_value(ticket.customer),
        tracking_number=ticket.tracking_number,
        channel=ticket.source_channel.value,
        source_chat_id=ticket.source_chat_id,
        assigned_to=ticket.assignee.display_name if ticket.assignee else None,
        required_action=ticket.required_action,
        missing_fields=ticket.missing_fields,
        last_customer_message=ticket.last_customer_message,
        customer_update=ticket.customer_update,
        resolution_summary=ticket.resolution_summary,
        last_human_update=ticket.last_human_update,
        created_at=ticket.created_at,
        last_updated=ticket.updated_at,
        requested_time=ticket.requested_time,
        destination=ticket.destination,
        preferred_reply_channel=ticket.preferred_reply_channel,
        preferred_reply_contact=ticket.preferred_reply_contact,
        market_id=ticket.market_id,
        country_code=ticket.country_code,
        ai_summary=(latest_ai.summary if latest_ai else ticket.ai_summary),
        ai_case_type=(latest_ai.classification if latest_ai else ticket.ai_classification),
        ai_suggested_required_action=latest_ai.recommended_action if latest_ai else ticket.required_action,
        ai_missing_fields=(", ".join(json.loads(latest_ai.missing_fields_json)) if latest_ai and latest_ai.missing_fields_json else ticket.missing_fields),
    )


def serialize_lite_list(ticket: Ticket, highlighted: bool = False) -> LiteCaseListItem:
    return LiteCaseListItem(
        id=ticket.id,
        case=ticket.ticket_no,
        case_type=ticket.case_type or ticket.category or ticket.ai_classification,
        issue_summary=ticket.issue_summary or ticket.title,
        status=_internal_to_lite(ticket.status),
        priority=ticket.priority.value,
        tracking_number=ticket.tracking_number,
        customer_contact=_contact_value(ticket.customer),
        assigned_to=ticket.assignee.display_name if ticket.assignee else None,
        last_updated=ticket.updated_at,
        highlighted=highlighted,
    )


def _find_open_case(db: Session, payload: LiteCaseCreate) -> Optional[Ticket]:
    q = db.query(Ticket)
    q = q.filter(Ticket.status.notin_([TicketStatus.resolved, TicketStatus.closed, TicketStatus.canceled]))

    if payload.source_chat_id:
        found = q.filter(Ticket.source_chat_id == payload.source_chat_id).order_by(Ticket.updated_at.desc()).first()
        if found:
            return found

    if payload.tracking_number and payload.customer_contact:
        found = (
            q.join(Customer, Customer.id == Ticket.customer_id, isouter=True)
            .filter(
                Ticket.tracking_number == payload.tracking_number,
                or_(Customer.phone == payload.customer_contact, Customer.email == payload.customer_contact),
            )
            .order_by(Ticket.updated_at.desc())
            .first()
        )
        if found:
            return found

    return None


def list_lite_cases(db: Session, current_user: User, q: Optional[str] = None, status: Optional[str] = None):
    status_value = None
    status_in = None
    if status:
        if status == "pending_human":
            status_in = [TicketStatus.new.value, TicketStatus.pending_assignment.value, TicketStatus.waiting_internal.value, TicketStatus.escalated.value]
        elif status == "closed":
            status_in = [s.value for s in TicketStatus if s not in {TicketStatus.new, TicketStatus.pending_assignment, TicketStatus.waiting_internal, TicketStatus.escalated, TicketStatus.in_progress, TicketStatus.waiting_customer, TicketStatus.resolved}]
        else:
            mapping = {
                "new": TicketStatus.new,
                "in_progress": TicketStatus.in_progress,
                "waiting_customer": TicketStatus.waiting_customer,
                "resolved": TicketStatus.resolved,
            }
            internal_status = mapping.get(status)
            if internal_status:
                status_value = internal_status.value

    tickets = list_tickets(db, current_user, q=q, status_value=status_value, status_in=status_in, limit=100)
    return [serialize_lite_list(t) for t in tickets]


def get_lite_case(db: Session, ticket_id: int, current_user: User) -> LiteCaseDetail:
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_visible(current_user, ticket, db)
    return serialize_lite_case(ticket)


def create_lite_case(db: Session, payload: LiteCaseCreate, current_user: User):
    if payload.upsert_open_case:
        existing = _find_open_case(db, payload)
        if existing:
            changed = False
            for field, value in {
                "last_customer_message": payload.last_customer_message,
                "customer_request": payload.customer_request,
                "required_action": payload.required_action,
                "missing_fields": payload.missing_fields,
                "customer_update": payload.customer_update,
            }.items():
                if value is not None and getattr(existing, field) != value:
                    setattr(existing, field, value)
                    changed = True
            if payload.issue_summary and existing.issue_summary != payload.issue_summary:
                existing.issue_summary = payload.issue_summary
                existing.title = payload.issue_summary[:255]
                changed = True
            if changed:
                existing.updated_at = utc_now()
                log_event(
                    db,
                    ticket_id=existing.id,
                    actor_id=current_user.id,
                    event_type=EventType.field_updated,
                    note="Case updated from intake",
                    payload={"summary": "Open case updated from new intake"},
                )
                db.flush()
            return serialize_lite_case(get_ticket_or_404(db, existing.id)), "updated"

    customer = None
    if payload.customer_name or payload.customer_contact:
        customer = CustomerInput(
            name=payload.customer_name or "Unknown Customer",
            phone=payload.customer_contact if payload.customer_contact and payload.customer_contact.startswith("+") else None,
            email=payload.customer_contact if payload.customer_contact and "@" in payload.customer_contact else None,
        )

    ticket = create_ticket(
        db,
        TicketCreate(
            title=payload.issue_summary[:255],
            description=payload.customer_request,
            source=TicketSource.ai_intake if payload.ai_summary else TicketSource.manual,
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

    return serialize_lite_case(ticket), "created"


def update_lite_case(db: Session, ticket_id: int, payload: LiteCaseUpdate, current_user: User) -> LiteCaseDetail:
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_visible(current_user, ticket, db)

    changed_fields = []
    mapping = {
        "case_type": "case_type",
        "issue_summary": "issue_summary",
        "customer_request": "customer_request",
        "tracking_number": "tracking_number",
        "required_action": "required_action",
        "missing_fields": "missing_fields",
        "last_customer_message": "last_customer_message",
        "customer_update": "customer_update",
        "resolution_summary": "resolution_summary",
        "requested_time": "requested_time",
        "destination": "destination",
        "preferred_reply_channel": "preferred_reply_channel",
        "preferred_reply_contact": "preferred_reply_contact",
        "market_id": "market_id",
        "country_code": "country_code",
    }
    for req_field, model_field in mapping.items():
        value = getattr(payload, req_field)
        if value is not None and getattr(ticket, model_field) != value:
            setattr(ticket, model_field, value)
            changed_fields.append(req_field)

    if payload.issue_summary is not None:
        ticket.title = payload.issue_summary[:255]
    if payload.customer_request is not None:
        ticket.description = payload.customer_request
    if payload.case_type is not None:
        ticket.category = payload.case_type
    if payload.priority is not None:
        ticket.priority = _priority(payload.priority)
        changed_fields.append("priority")

    if changed_fields:
        ticket.updated_at = utc_now()
        log_event(
            db,
            ticket_id=ticket.id,
            actor_id=current_user.id,
            event_type=EventType.field_updated,
            note="Lite case fields updated",
            payload={"fields": changed_fields, "summary": "Case details updated"},
        )
        db.flush()
    return serialize_lite_case(get_ticket_or_404(db, ticket.id))


def assign_lite_case(db: Session, ticket_id: int, payload: LiteAssignRequest, current_user: User) -> LiteCaseDetail:
    ensure_can_assign(current_user, db)
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_visible(current_user, ticket, db)
    assignee, team = validate_assignee_team(db, payload.assignee_id, payload.team_id, fallback_team_id=ticket.team_id)
    ticket.assignee_id = assignee.id if assignee else None
    ticket.team_id = team.id if team else ticket.team_id
    if ticket.status in {TicketStatus.new, TicketStatus.pending_assignment} and ticket.assignee_id:
        ticket.status = TicketStatus.in_progress
    ticket.updated_at = utc_now()
    log_event(
        db,
        ticket_id=ticket.id,
        actor_id=current_user.id,
        event_type=EventType.assigned,
        note="Lite assignment updated",
        payload={"summary": f"Assigned to {assignee.display_name if assignee else 'unassigned'}"},
    )
    db.flush()
    return serialize_lite_case(get_ticket_or_404(db, ticket.id))


def change_lite_status(db: Session, ticket_id: int, payload: LiteStatusRequest, current_user: User) -> LiteCaseDetail:
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_visible(current_user, ticket, db)
    internal = _lite_status_to_internal(ticket, payload.status)
    ticket = change_status(db, ticket_id, TicketStatusChangeRequest(new_status=internal), current_user)

    return serialize_lite_case(ticket)


def workflow_update_lite_case(db: Session, ticket_id: int, payload: LiteWorkflowUpdateRequest, current_user: User) -> LiteCaseDetail:
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_visible(current_user, ticket, db)

    provided_fields = payload.model_fields_set
    assignee_provided = "assignee_id" in provided_fields
    team_provided = "team_id" in provided_fields
    status_provided = "status" in provided_fields and payload.status is not None

    if team_provided and payload.team_id is None:
        raise HTTPException(status_code=400, detail="team_id cannot be null in workflow_update")

    if assignee_provided or team_provided:
        ensure_can_assign(current_user, db)
        effective_team_id = payload.team_id if team_provided else ticket.team_id
        effective_assignee_id = payload.assignee_id if assignee_provided else ticket.assignee_id
        assignee, team = validate_assignee_team(
            db,
            effective_assignee_id,
            effective_team_id,
            fallback_team_id=ticket.team_id,
        )
        if team_provided and team is not None:
            ticket.team_id = team.id
        if assignee_provided:
            ticket.assignee_id = assignee.id if assignee else None
        if ticket.status in {TicketStatus.new, TicketStatus.pending_assignment} and ticket.assignee_id:
            ticket.status = TicketStatus.in_progress
        summary_name = assignee.display_name if assignee else (ticket.assignee.display_name if ticket.assignee else "unassigned")
        log_event(
            db,
            ticket_id=ticket.id,
            actor_id=current_user.id,
            event_type=EventType.assigned,
            note="Lite workflow assignment updated",
            payload={"summary": f"Assigned to {summary_name}"},
        )

    changed_fields = []
    for field in ["required_action", "missing_fields", "customer_update", "resolution_summary"]:
        value = getattr(payload, field)
        if value is not None and getattr(ticket, field) != value:
            setattr(ticket, field, value)
            changed_fields.append(field)

    if changed_fields:
        log_event(
            db,
            ticket_id=ticket.id,
            actor_id=current_user.id,
            event_type=EventType.field_updated,
            note="Lite workflow fields updated",
            payload={"fields": changed_fields, "summary": "Workflow fields updated"},
        )

    if payload.human_note:
        note = TicketInternalNote(ticket_id=ticket.id, author_id=current_user.id, body=payload.human_note)
        db.add(note)
        ticket.last_human_update = payload.human_note
        log_event(
            db,
            ticket_id=ticket.id,
            actor_id=current_user.id,
            event_type=EventType.internal_note_added,
            note="Internal note added",
        )

    status_changed = False
    if status_provided:
        internal = _lite_status_to_internal(ticket, payload.status or "")
        status_changed = internal != ticket.status
        if status_changed:
            ensure_can_change_status(current_user, ticket, internal, db)
            validate_transition(ticket.status, internal)
            if requires_note(internal) and not payload.human_note:
                raise HTTPException(status_code=400, detail="This status change requires a note in workflow_update")
            if internal == TicketStatus.closed and ticket.resolution_category == ResolutionCategory.none:
                raise HTTPException(status_code=400, detail="Resolution category is required before closing a ticket")
            old_status = ticket.status
            ticket.status = internal
            if internal == TicketStatus.resolved:
                ticket.resolved_at = utc_now()
            if internal in {TicketStatus.closed, TicketStatus.canceled}:
                ticket.closed_at = utc_now()
                resume_sla(ticket)
            update_pause_state_for_status(ticket, internal, db)
            evaluate_sla(ticket, db)
            log_event(
                db,
                ticket_id=ticket.id,
                actor_id=current_user.id,
                event_type=EventType.status_changed,
                old_value=old_status.value,
                new_value=ticket.status.value,
                note=payload.human_note,
                payload={"summary": f"Status changed from {old_status.value} to {ticket.status.value}"},
            )

    ticket.updated_at = utc_now()
    db.flush()
    db.refresh(ticket)

    return serialize_lite_case(ticket)


def save_human_note_lite(db: Session, ticket_id: int, payload: LiteHumanNoteRequest, current_user: User) -> LiteCaseDetail:
    add_internal_note(db, ticket_id, InternalNoteCreate(body=payload.note), current_user)
    ticket = get_ticket_or_404(db, ticket_id)
    ticket.last_human_update = payload.note
    ticket.updated_at = utc_now()
    db.flush()
    return serialize_lite_case(get_ticket_or_404(db, ticket.id))


def save_ai_intake_lite(db: Session, ticket_id: int, payload: LiteAIIntakeRequest, current_user: User) -> LiteCaseDetail:
    add_ai_intake(
        db,
        ticket_id,
        AIIntakeCreate(
            summary=payload.ai_summary or "",
            classification=payload.case_type,
            confidence=None,
            missing_fields=[x.strip() for x in (payload.missing_fields or "").split(",") if x.strip()],
            recommended_action=payload.suggested_required_action,
            suggested_reply=None,
            raw_payload=None,
            human_override_reason=None,
        ),
        current_user,
    )
    ticket = get_ticket_or_404(db, ticket_id)
    if payload.ai_summary is not None:
        ticket.ai_summary = payload.ai_summary
    if payload.case_type is not None:
        ticket.ai_classification = payload.case_type
        ticket.case_type = payload.case_type
        ticket.category = payload.case_type
    if payload.suggested_required_action is not None:
        ticket.required_action = payload.suggested_required_action
    if payload.missing_fields is not None:
        ticket.missing_fields = payload.missing_fields
    if payload.last_customer_message is not None:
        ticket.last_customer_message = payload.last_customer_message
    ticket.updated_at = utc_now()
    db.flush()
    return serialize_lite_case(get_ticket_or_404(db, ticket.id))
