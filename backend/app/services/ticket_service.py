from __future__ import annotations

import json
import uuid
from typing import Optional

from sqlalchemy import case as sql_case

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from ..enums import EventType, MessageStatus, NoteVisibility, ResolutionCategory, TicketPriority, TicketStatus, UserRole
from ..models import (
    Customer,
    SLAPolicy,
    Tag,
    Team,
    Ticket,
    TicketAIIntake,
    TicketAttachment,
    TicketComment,
    TicketEvent,
    TicketFollower,
    TicketInternalNote,
    TicketOutboundMessage,
    TicketTag,
    User,
)
from ..schemas import (
    AIIntakeCreate,
    AttachmentRead,
    CommentCreate,
    InternalNoteCreate,
    OutboundDraftCreate,
    OutboundSendRequest,
    TicketAssignRequest,
    TicketCreate,
    TicketEscalateRequest,
    TicketReopenRequest,
    TicketStatusChangeRequest,
    TicketUpdate,
)
from .audit_service import log_event
from .file_service import build_attachment_download_url, save_upload
from .message_dispatch import queue_outbound_message
from .permissions import (
    ensure_can_assign,
    ensure_can_change_status,
    ensure_can_escalate,
    ensure_can_update_core_fields,
    ensure_can_upload_attachment,
    ensure_can_write_ai_intake,
    ensure_can_write_internal_note,
    ensure_can_write_comment,
    ensure_can_read_customer_profile,
    ensure_can_save_outbound_draft,
    ensure_can_send_outbound,
    ensure_ticket_visible,
)
from ..utils.normalize import normalize_email, normalize_phone
from ..utils.time import ensure_utc, utc_now
from .sla_service import (
    apply_policy_to_ticket,
    compute_sla_snapshot,
    evaluate_sla,
    get_policy_for_priority,
    resume_sla,
    seed_default_sla_policies,
    update_first_response,
    update_pause_state_for_status,
)
from .state_machine import is_terminal, requires_note, validate_transition


def generate_ticket_no() -> str:
    stamp = utc_now().strftime("%Y%m%d%H%M%S")
    suffix = uuid.uuid4().hex[:6].upper()
    return f"CS-{stamp}-{suffix}"


def get_user_or_404(db: Session, user_id: int) -> User:
    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def get_team_or_404(db: Session, team_id: int) -> Team:
    team = db.query(Team).filter(Team.id == team_id, Team.is_active.is_(True)).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return team


def get_ticket_or_404(db: Session, ticket_id: int) -> Ticket:
    ticket = (
        db.query(Ticket)
        .populate_existing()
        .options(
            joinedload(Ticket.customer),
            joinedload(Ticket.assignee),
            joinedload(Ticket.team),
            joinedload(Ticket.comments),
            joinedload(Ticket.internal_notes),
            joinedload(Ticket.attachments),
            joinedload(Ticket.outbound_messages),
            joinedload(Ticket.ai_intakes),
        )
        .filter(Ticket.id == ticket_id)
        .first()
    )
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


def _apply_customer_normalization(customer: Customer) -> None:
    customer.email_normalized = normalize_email(customer.email)
    customer.phone_normalized = normalize_phone(customer.phone)


def _customer_match_query(db: Session, *, email: Optional[str], phone: Optional[str], external_ref: Optional[str]):
    email_norm = normalize_email(email)
    phone_norm = normalize_phone(phone)
    if external_ref:
        matches = db.query(Customer).filter(Customer.external_ref == external_ref).all()
        if len(matches) > 1:
            raise HTTPException(status_code=409, detail="Multiple customers matched external_ref")
        if matches:
            return matches[0]
    if phone_norm:
        matches = db.query(Customer).filter(Customer.phone_normalized == phone_norm).all()
        if len(matches) > 1:
            raise HTTPException(status_code=409, detail="Multiple customers matched phone")
        if matches:
            return matches[0]
    if email_norm:
        matches = db.query(Customer).filter(Customer.email_normalized == email_norm).all()
        if len(matches) > 1:
            raise HTTPException(status_code=409, detail="Multiple customers matched email")
        if matches:
            return matches[0]
    return None


def resolve_customer(db: Session, payload: TicketCreate) -> Optional[Customer]:
    if payload.customer_id:
        customer = db.query(Customer).filter(Customer.id == payload.customer_id).first()
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")
        return customer

    if not payload.customer:
        return None

    existing = _customer_match_query(
        db,
        email=payload.customer.email,
        phone=payload.customer.phone,
        external_ref=payload.customer.external_ref,
    )
    if existing:
        if payload.customer.name:
            existing.name = payload.customer.name
        if payload.customer.email:
            existing.email = payload.customer.email
        if payload.customer.phone:
            existing.phone = payload.customer.phone
        if payload.customer.external_ref:
            existing.external_ref = payload.customer.external_ref
        _apply_customer_normalization(existing)
        return existing

    customer = Customer(
        name=payload.customer.name,
        email=payload.customer.email,
        phone=payload.customer.phone,
        external_ref=payload.customer.external_ref,
    )
    _apply_customer_normalization(customer)
    db.add(customer)
    db.flush()
    return customer


def attach_tags(db: Session, ticket: Ticket, tag_ids: list[int]):
    db.query(TicketTag).filter(TicketTag.ticket_id == ticket.id).delete()
    if not tag_ids:
        return
    tags = db.query(Tag).filter(Tag.id.in_(tag_ids)).all()
    existing_ids = {tag.id for tag in tags}
    missing = set(tag_ids) - existing_ids
    if missing:
        raise HTTPException(status_code=404, detail=f"Tag not found: {sorted(missing)}")
    for tag_id in tag_ids:
        db.add(TicketTag(ticket_id=ticket.id, tag_id=tag_id))
    db.flush()


def validate_assignee_team(
    db: Session,
    assignee_id: Optional[int],
    team_id: Optional[int],
    *,
    fallback_team_id: Optional[int] = None,
):
    assignee = None
    team = None
    effective_team_id = team_id if team_id is not None else fallback_team_id
    if effective_team_id is not None:
        team = get_team_or_404(db, effective_team_id)
    if assignee_id is not None:
        assignee = get_user_or_404(db, assignee_id)
    if assignee and team and assignee.team_id != team.id and assignee.role not in {UserRole.admin, UserRole.manager}:
        raise HTTPException(status_code=400, detail="Assignee does not belong to selected team")
    return assignee, team


def create_ticket(db: Session, payload: TicketCreate, current_user: User) -> Ticket:
    if payload.team_id or payload.assignee_id:
        validate_assignee_team(db, payload.assignee_id, payload.team_id, fallback_team_id=payload.team_id or current_user.team_id)

    customer = resolve_customer(db, payload)
    ticket = Ticket(
        ticket_no=generate_ticket_no(),
        title=payload.title,
        description=payload.description,
        source=payload.source,
        source_channel=payload.source_channel,
        priority=payload.priority,
        status=TicketStatus.pending_assignment if not payload.assignee_id else TicketStatus.in_progress,
        category=payload.category,
        sub_category=payload.sub_category,
        tracking_number=payload.tracking_number,
        customer_id=customer.id if customer else None,
        assignee_id=payload.assignee_id,
        team_id=payload.team_id or current_user.team_id,
        created_by=current_user.id,
        market_id=payload.market_id,
        country_code=payload.country_code,
        ai_summary=payload.ai_summary,
        ai_classification=payload.ai_classification,
        ai_confidence=payload.ai_confidence,
        case_type=payload.case_type,
        issue_summary=payload.issue_summary,
        customer_request=payload.customer_request,
        source_chat_id=payload.source_chat_id,
        required_action=payload.required_action,
        missing_fields=payload.missing_fields,
        last_customer_message=payload.last_customer_message,
        customer_update=payload.customer_update,
        resolution_summary=payload.resolution_summary,
        last_human_update=payload.last_human_update,
        requested_time=payload.requested_time,
        destination=payload.destination,
        preferred_reply_channel=payload.preferred_reply_channel,
        preferred_reply_contact=payload.preferred_reply_contact,
    )
    db.add(ticket)
    db.flush()

    policy = get_policy_for_priority(db, ticket.priority)
    if policy:
        ticket.sla_policy_id = policy.id
        apply_policy_to_ticket(ticket, policy)

    attach_tags(db, ticket, payload.tag_ids)

    log_event(
        db,
        ticket_id=ticket.id,
        actor_id=current_user.id,
        event_type=EventType.ticket_created,
        note="Ticket created",
        payload={
            "ticket_no": ticket.ticket_no,
            "source": ticket.source.value,
            "source_channel": ticket.source_channel.value,
        },
    )

    if payload.ai_summary or payload.ai_classification or payload.ai_confidence is not None:
        ai_intake = TicketAIIntake(
            ticket_id=ticket.id,
            summary=payload.ai_summary or payload.description,
            classification=payload.ai_classification,
            confidence=payload.ai_confidence,
            created_by=current_user.id,
            market_id=payload.market_id,
            country_code=payload.country_code,
        )
        db.add(ai_intake)
        db.flush()
        log_event(
            db,
            ticket_id=ticket.id,
            actor_id=current_user.id,
            event_type=EventType.ai_intake_added,
            note="AI intake captured on ticket creation",
            payload={"confidence": payload.ai_confidence, "classification": payload.ai_classification},
        )

    db.flush()
    db.refresh(ticket)
    return get_ticket_or_404(db, ticket.id)


def list_tickets(
    db: Session,
    current_user: User,
    *,
    q: Optional[str] = None,
    status_value: Optional[str] = None,
    status_in: Optional[list[str]] = None,
    priority_value: Optional[str] = None,
    assignee_id: Optional[int] = None,
    team_id: Optional[int] = None,
    overdue: Optional[bool] = None,
    limit: int = 50,
    skip: int = 0,
) -> list[Ticket]:
    query = db.query(Ticket).options(joinedload(Ticket.customer), joinedload(Ticket.assignee), joinedload(Ticket.team))
    if current_user.role not in {UserRole.admin, UserRole.manager, UserRole.auditor}:
        query = query.filter(or_(Ticket.team_id == current_user.team_id, Ticket.assignee_id == current_user.id))

    if q:
        like = f"%{q}%"
        query = query.outerjoin(Customer, Customer.id == Ticket.customer_id).filter(
            or_(
                Ticket.ticket_no.ilike(like),
                Ticket.title.ilike(like),
                Ticket.description.ilike(like),
                Customer.name.ilike(like),
                Ticket.tracking_number.ilike(like),
            )
        )
    if status_value:
        query = query.filter(Ticket.status == status_value)
    if status_in:
        query = query.filter(Ticket.status.in_(status_in))
    if priority_value:
        query = query.filter(Ticket.priority == priority_value)
    if assignee_id:
        query = query.filter(Ticket.assignee_id == assignee_id)
    if team_id:
        query = query.filter(Ticket.team_id == team_id)
    if overdue is True:
        query = query.filter(Ticket.resolution_due_at.is_not(None), Ticket.resolution_due_at < utc_now(), Ticket.status.notin_([TicketStatus.closed, TicketStatus.canceled]))
    tickets = query.order_by(Ticket.updated_at.desc()).offset(skip).limit(limit).all()
    return tickets


def update_ticket(db: Session, ticket_id: int, payload: TicketUpdate, current_user: User) -> Ticket:
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_visible(current_user, ticket, db)
    ensure_can_update_core_fields(current_user, db)

    changed = False
    for field in [
        "title", "description", "category", "sub_category", "resolution_category",
        "case_type", "issue_summary", "customer_request", "source_chat_id", "required_action",
        "missing_fields", "last_customer_message", "customer_update", "resolution_summary",
        "last_human_update", "requested_time", "destination", "preferred_reply_channel",
        "preferred_reply_contact", "market_id", "country_code",
    ]:
        value = getattr(payload, field)
        if value is not None and getattr(ticket, field) != value:
            old = getattr(ticket, field)
            setattr(ticket, field, value)
            log_event(
                db,
                ticket_id=ticket.id,
                actor_id=current_user.id,
                event_type=EventType.field_updated,
                field_name=field,
                old_value=str(old) if old is not None else None,
                new_value=str(value),
                payload={"summary": f"{field} updated"},
            )
            changed = True

    if payload.priority is not None and ticket.priority != payload.priority:
        old = ticket.priority
        ticket.priority = payload.priority
        policy = get_policy_for_priority(db, ticket.priority)
        if policy:
            ticket.sla_policy_id = policy.id
            apply_policy_to_ticket(ticket, policy)
        log_event(
            db,
            ticket_id=ticket.id,
            actor_id=current_user.id,
            event_type=EventType.field_updated,
            field_name="priority",
            old_value=old.value,
            new_value=ticket.priority.value,
            payload={"summary": "Priority updated and SLA recalculated"},
        )
        changed = True

    if payload.tag_ids is not None:
        attach_tags(db, ticket, payload.tag_ids)
        log_event(
            db,
            ticket_id=ticket.id,
            actor_id=current_user.id,
            event_type=EventType.field_updated,
            field_name="tags",
            note="Tags updated",
            payload={"tag_ids": payload.tag_ids},
        )
        changed = True

    if changed:
        ticket.updated_at = utc_now()
        db.flush()
    db.refresh(ticket)
    return get_ticket_or_404(db, ticket.id)


def assign_ticket(db: Session, ticket_id: int, payload: TicketAssignRequest, current_user: User) -> Ticket:
    ensure_can_assign(current_user, db)
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_visible(current_user, ticket, db)

    assignee, team = validate_assignee_team(db, payload.assignee_id, payload.team_id, fallback_team_id=ticket.team_id)
    old_assignee_id = ticket.assignee_id
    old_team_id = ticket.team_id

    ticket.assignee_id = assignee.id if assignee else None
    ticket.team_id = team.id if team else ticket.team_id
    if ticket.status in {TicketStatus.new, TicketStatus.pending_assignment} and ticket.assignee_id:
        ticket.status = TicketStatus.in_progress

    log_event(
        db,
        ticket_id=ticket.id,
        actor_id=current_user.id,
        event_type=EventType.assigned,
        note=payload.note,
        payload={
            "old_assignee_id": old_assignee_id,
            "new_assignee_id": ticket.assignee_id,
            "assignee_name": assignee.display_name if assignee else None,
            "old_team_id": old_team_id,
            "new_team_id": ticket.team_id,
            "team_name": team.name if team else None,
            "summary": f"Assigned to {assignee.display_name if assignee else 'unassigned'}",
        },
    )
    db.flush()
    return get_ticket_or_404(db, ticket.id)


def change_status(db: Session, ticket_id: int, payload: TicketStatusChangeRequest, current_user: User) -> Ticket:
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_visible(current_user, ticket, db)
    ensure_can_change_status(current_user, ticket, payload.new_status, db)
    validate_transition(ticket.status, payload.new_status)

    if requires_note(payload.new_status) and not payload.note:
        raise HTTPException(status_code=400, detail="This status change requires a note")

    if payload.new_status == TicketStatus.closed and ticket.resolution_category == ResolutionCategory.none:
        raise HTTPException(status_code=400, detail="Resolution category is required before closing a ticket")

    old_status = ticket.status
    ticket.status = payload.new_status

    if payload.new_status == TicketStatus.resolved:
        ticket.resolved_at = utc_now()
    if payload.new_status == TicketStatus.closed:
        ticket.closed_at = utc_now()
        resume_sla(ticket)
    if payload.new_status == TicketStatus.canceled:
        ticket.closed_at = utc_now()
        resume_sla(ticket)

    update_pause_state_for_status(ticket, payload.new_status, db)
    evaluate_sla(ticket, db)

    log_event(
        db,
        ticket_id=ticket.id,
        actor_id=current_user.id,
        event_type=EventType.status_changed,
        old_value=old_status.value,
        new_value=ticket.status.value,
        note=payload.note,
        payload={"summary": f"Status changed from {old_status.value} to {ticket.status.value}"},
    )
    db.flush()
    return get_ticket_or_404(db, ticket.id)


def escalate_ticket(db: Session, ticket_id: int, payload: TicketEscalateRequest, current_user: User) -> Ticket:
    ensure_can_escalate(current_user, db)
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_visible(current_user, ticket, db)
    team = get_team_or_404(db, payload.team_id)
    old_team_id = ticket.team_id
    old_status = ticket.status

    ticket.team_id = team.id
    ticket.assignee_id = None
    ticket.status = TicketStatus.escalated
    evaluate_sla(ticket, db)

    log_event(
        db,
        ticket_id=ticket.id,
        actor_id=current_user.id,
        event_type=EventType.escalated,
        note=payload.note,
        old_value=str(old_team_id) if old_team_id else None,
        new_value=str(team.id),
        payload={"team_name": team.name, "from_status": old_status.value, "summary": f"Escalated to {team.name}"},
    )
    db.flush()
    return get_ticket_or_404(db, ticket.id)


def reopen_ticket(db: Session, ticket_id: int, payload: TicketReopenRequest, current_user: User) -> Ticket:
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_visible(current_user, ticket, db)
    if ticket.status not in {TicketStatus.closed, TicketStatus.canceled, TicketStatus.resolved}:
        raise HTTPException(status_code=400, detail="Only resolved, closed, or canceled tickets can be reopened")

    previous_assignee_id = ticket.assignee_id
    previous_team_id = ticket.team_id

    ticket.reopen_count += 1
    ticket.closed_at = None
    ticket.resolved_at = None

    if payload.assign_to_previous and previous_assignee_id:
        ticket.assignee_id = previous_assignee_id
        ticket.status = TicketStatus.in_progress
    else:
        ticket.assignee_id = None
        ticket.status = TicketStatus.pending_assignment

    if payload.restore_team and previous_team_id:
        ticket.team_id = previous_team_id

    policy = get_policy_for_priority(db, ticket.priority)
    if policy:
        ticket.sla_policy_id = policy.id
        apply_policy_to_ticket(ticket, policy)
    resume_sla(ticket)
    evaluate_sla(ticket, db)

    log_event(
        db,
        ticket_id=ticket.id,
        actor_id=current_user.id,
        event_type=EventType.reopened,
        note=payload.reason,
        payload={
            "assign_to_previous": payload.assign_to_previous,
            "restore_team": payload.restore_team,
            "restored_assignee_id": previous_assignee_id if payload.assign_to_previous else None,
            "restored_team_id": previous_team_id if payload.restore_team else None,
            "summary": "Ticket reopened and routed back to active workflow",
        },
    )
    db.flush()
    return get_ticket_or_404(db, ticket.id)


def add_comment(db: Session, ticket_id: int, payload: CommentCreate, current_user: User) -> TicketComment:
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_visible(current_user, ticket, db)
    ensure_can_write_comment(current_user, payload.visibility, db)
    comment = TicketComment(
        ticket_id=ticket.id,
        author_id=current_user.id,
        body=payload.body,
        visibility=payload.visibility,
    )
    db.add(comment)
    if payload.visibility == NoteVisibility.external:
        update_first_response(ticket)
    log_event(
        db,
        ticket_id=ticket.id,
        actor_id=current_user.id,
        event_type=EventType.comment_added,
        note="External comment added" if payload.visibility == NoteVisibility.external else "Internal comment added",
        payload={"visibility": payload.visibility.value},
    )
    evaluate_sla(ticket, db)
    db.flush()
    db.refresh(comment)
    return comment


def add_internal_note(db: Session, ticket_id: int, payload: InternalNoteCreate, current_user: User) -> TicketInternalNote:
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_visible(current_user, ticket, db)
    ensure_can_write_internal_note(current_user, db)
    note = TicketInternalNote(ticket_id=ticket.id, author_id=current_user.id, body=payload.body)
    db.add(note)
    log_event(
        db,
        ticket_id=ticket.id,
        actor_id=current_user.id,
        event_type=EventType.internal_note_added,
        note="Internal note added",
    )
    db.flush()
    db.refresh(note)
    return note


def add_attachment(
    db: Session,
    ticket_id: int,
    file: UploadFile,
    visibility: NoteVisibility,
    current_user: User,
) -> TicketAttachment:
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_visible(current_user, ticket, db)
    ensure_can_upload_attachment(current_user, db)
    stored = save_upload(file)
    attachment = TicketAttachment(
        ticket_id=ticket.id,
        uploaded_by=current_user.id,
        file_name=file.filename or stored.stored_name,
        storage_key=stored.storage_key,
        file_path=stored.file_path,
        file_url=None,
        mime_type=stored.mime_type,
        file_size=stored.file_size,
        visibility=visibility,
    )
    db.add(attachment)
    log_event(
        db,
        ticket_id=ticket.id,
        actor_id=current_user.id,
        event_type=EventType.attachment_added,
        note="Attachment uploaded",
        payload={"file_name": attachment.file_name, "visibility": visibility.value},
    )
    db.flush()
    db.refresh(attachment)
    attachment.file_url = build_attachment_download_url(attachment.id)
    return attachment


def save_outbound_draft(db: Session, ticket_id: int, payload: OutboundDraftCreate, current_user: User) -> TicketOutboundMessage:
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_visible(current_user, ticket, db)
    ensure_can_save_outbound_draft(current_user, db)
    draft = TicketOutboundMessage(
        ticket_id=ticket.id,
        channel=payload.channel,
        status=MessageStatus.draft,
        body=payload.body,
        provider_status="draft_saved",
        created_by=current_user.id,
    )
    db.add(draft)
    log_event(
        db,
        ticket_id=ticket.id,
        actor_id=current_user.id,
        event_type=EventType.outbound_draft_saved,
        note="Reply draft saved",
        payload={"channel": payload.channel.value, "summary": f"Draft saved for {payload.channel.value}"},
    )
    db.flush()
    db.refresh(draft)
    return draft


def send_outbound_message(db: Session, ticket_id: int, payload: OutboundSendRequest, current_user: User) -> TicketOutboundMessage:
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_visible(current_user, ticket, db)
    ensure_can_send_outbound(current_user, db)

    message = queue_outbound_message(
        db,
        ticket_id=ticket.id,
        channel=payload.channel,
        body=payload.body,
        created_by=current_user.id,
    )
    update_first_response(ticket)
    evaluate_sla(ticket, db)
    log_event(
        db,
        ticket_id=ticket.id,
        actor_id=current_user.id,
        event_type=EventType.outbound_queued,
        note="Reply queued for dispatch",
        payload={"channel": payload.channel.value, "provider_status": "queued"},
    )
    db.flush()
    db.refresh(message)
    return message


def add_ai_intake(db: Session, ticket_id: int, payload: AIIntakeCreate, current_user: User) -> TicketAIIntake:
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_visible(current_user, ticket, db)
    ensure_can_write_ai_intake(current_user, db)
    resolved_market_id = payload.market_id if payload.market_id is not None else ticket.market_id
    resolved_country_code = payload.country_code or ticket.country_code
    ai = TicketAIIntake(
        ticket_id=ticket.id,
        summary=payload.summary,
        classification=payload.classification,
        confidence=payload.confidence,
        missing_fields_json=json.dumps(payload.missing_fields, ensure_ascii=False),
        recommended_action=payload.recommended_action,
        suggested_reply=payload.suggested_reply,
        raw_payload_json=json.dumps(payload.raw_payload or {}, ensure_ascii=False),
        human_override_reason=payload.human_override_reason,
        created_by=current_user.id,
        market_id=resolved_market_id,
        country_code=resolved_country_code,
    )
    db.add(ai)
    ticket.ai_summary = payload.summary
    ticket.ai_classification = payload.classification
    ticket.ai_confidence = payload.confidence
    log_event(
        db,
        ticket_id=ticket.id,
        actor_id=current_user.id,
        event_type=EventType.ai_intake_added,
        note="AI intake added",
        payload={
            "classification": payload.classification,
            "confidence": payload.confidence,
            "summary": payload.summary,
        },
    )
    db.flush()
    db.refresh(ai)
    return ai


def get_customer_history(db: Session, customer_id: int, current_user: User):
    ensure_can_read_customer_profile(current_user, db)
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    ticket_query = db.query(Ticket).filter(Ticket.customer_id == customer.id)
    privileged_roles = {UserRole.admin, UserRole.manager, UserRole.auditor}
    if current_user.role not in privileged_roles:
        ticket_query = ticket_query.filter(or_(Ticket.team_id == current_user.team_id, Ticket.assignee_id == current_user.id))
    tickets = ticket_query.order_by(Ticket.updated_at.desc()).limit(10).all()
    total = ticket_query.count()
    if current_user.role not in privileged_roles and total == 0:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer, total, tickets


def get_ticket_events(db: Session, ticket_id: int, current_user: User) -> list[TicketEvent]:
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_visible(current_user, ticket, db)
    return db.query(TicketEvent).filter(TicketEvent.ticket_id == ticket.id).order_by(TicketEvent.created_at.desc()).all()


def get_ticket_stats(db: Session, current_user: User):
    base_query = db.query(Ticket)
    if current_user.role not in {UserRole.admin, UserRole.manager, UserRole.auditor}:
        base_query = base_query.filter(or_(Ticket.team_id == current_user.team_id, Ticket.assignee_id == current_user.id))

    total = base_query.count()
    overdue_count = (
        base_query.filter(
            Ticket.resolution_due_at.is_not(None),
            Ticket.resolution_due_at < utc_now(),
            Ticket.status.notin_([TicketStatus.closed, TicketStatus.canceled]),
        ).count()
    )
    my_open_count = (
        base_query.filter(
            Ticket.assignee_id == current_user.id,
            Ticket.status.notin_([TicketStatus.closed, TicketStatus.canceled]),
        ).count()
    )
    status_rows = base_query.with_entities(Ticket.status, func.count(Ticket.id)).group_by(Ticket.status).all()
    by_status = {status.value if hasattr(status, "value") else str(status): count for status, count in status_rows}
    return {
        "total": total,
        "overdue_count": overdue_count,
        "my_open_count": my_open_count,
        "by_status": by_status,
    }
