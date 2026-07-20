from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from ..db import get_db
from ..enums import NoteVisibility, SourceChannel
from ..models import Ticket, Tag, TicketTag, TicketOutboundMessage
from ..schemas import (
    AIIntakeCreate,
    AIIntakeRead,
    AttachmentRead,
    CommentCreate,
    CommentRead,
    InternalNoteCreate,
    InternalNoteRead,
    EmailDeliveryReceiptRequest,
    EmailDeliveryReceiptResponse,
    InboundEmailIngestRequest,
    InboundEmailIngestResponse,
    InboundEmailMessageRead,
    OutboundDraftCreate,
    OutboundMessageRead,
    OutboundSendRequest,
    TicketAssignRequest,
    TicketCreate,
    TicketEscalateRequest,
    TicketListItem,
    TicketRead,
    TicketReopenRequest,
    TicketStatusChangeRequest,
    TicketUpdate,
    TimelineItemRead,
    UserRead,
    TeamRead,
    CustomerRead,
    TagRead,
    MarketBulletinRead,
)
from ..services.ticket_service import (
    add_ai_intake,
    add_attachment,
    add_comment,
    add_internal_note,
    assign_ticket,
    change_status,
    create_ticket,
    escalate_ticket,
    get_ticket_events,
    get_ticket_or_404,
    list_tickets,
    reopen_ticket,
    save_outbound_draft,
    send_outbound_message,
    update_ticket,
)
from ..services.email_delivery_receipt_service import record_email_delivery_receipt
from ..services.email_inbound_service import ingest_ticket_inbound_email
from ..services.timeline_service import build_unified_timeline
from ..services.permissions import ensure_ticket_visible
from ..services.sla_service import compute_sla_snapshot
from ..services.outbound_channel_registry import list_outbound_channel_capabilities, require_outbound_channel_sendable
from ..services.outbound_semantics import outbound_is_external_send, outbound_ui_label
from ..settings import get_settings
from .deps import get_current_user
from ..utils.time import format_utc
from ..unit_of_work import managed_session
from ..services.bulletin_service import list_active_bulletins

router = APIRouter(prefix="/api/tickets", tags=["tickets"])


def _serialize_attachment(row) -> dict:
    return {
        "id": row.id,
        "file_name": row.file_name,
        "download_url": row.download_url,
        "mime_type": row.mime_type,
        "file_size": row.file_size,
        "visibility": row.visibility,
        "created_at": row.created_at,
    }


def _serialize_outbound_message(row: TicketOutboundMessage) -> dict:
    settings = get_settings()
    channel_value = row.channel.value if hasattr(row.channel, "value") else str(row.channel)
    external_send = outbound_is_external_send(row.channel, row.provider_status)
    if external_send:
        delivery_semantics = "external_provider_send"
    elif channel_value == SourceChannel.web_chat.value:
        delivery_semantics = "local_webchat_delivery"
    else:
        delivery_semantics = "local_or_non_dispatchable"
    return {
        "id": row.id,
        "ticket_id": row.ticket_id,
        "channel": row.channel,
        "status": row.status,
        "subject": getattr(row, "subject", None),
        "body": row.body,
        "provider_status": row.provider_status,
        "provider_message_id": row.provider_message_id,
        "mailbox_thread_id": row.mailbox_thread_id,
        "mailbox_message_id": row.mailbox_message_id,
        "mailbox_references": row.mailbox_references,
        "error_message": row.error_message,
        "retry_count": row.retry_count,
        "max_retries": row.max_retries,
        "sent_at": row.sent_at,
        "created_at": row.created_at,
        "failure_code": getattr(row, "failure_code", None),
        "failure_reason": getattr(row, "failure_reason", None),
        "delivery_status": getattr(row, "delivery_status", None),
        "delivery_event_type": getattr(row, "delivery_event_type", None),
        "delivery_receipt_provider": getattr(row, "delivery_receipt_provider", None),
        "delivery_receipt_id": getattr(row, "delivery_receipt_id", None),
        "delivery_receipt_at": getattr(row, "delivery_receipt_at", None),
        "delivery_detail": getattr(row, "delivery_detail", None),
        "external_send": external_send,
        "delivery_semantics": delivery_semantics,
        "dispatch_enabled": bool(settings.enable_outbound_dispatch),
        "outbound_provider": settings.outbound_provider,
        "ui_label": outbound_ui_label(row.channel, row.status, row.provider_status),
        "operator_note": "Queued for external provider dispatch; wait for sent/dead/review final state" if external_send else "Local-only delivery; no external provider send occurred",
        "attachments": [_serialize_attachment(attachment) for attachment in getattr(row, "attachments", [])],
        "attachment_ids": [attachment.id for attachment in getattr(row, "attachments", [])],
        "attachments_count": len(getattr(row, "attachments", [])),
    }


def _serialize_ticket(ticket: Ticket, db: Session) -> TicketRead:
    tag_rows = db.query(Tag).join(TicketTag, TicketTag.tag_id == Tag.id).filter(TicketTag.ticket_id == ticket.id).all()
    return TicketRead(
        id=ticket.id,
        ticket_no=ticket.ticket_no,
        title=ticket.title,
        description=ticket.description,
        source=ticket.source,
        source_channel=ticket.source_channel,
        priority=ticket.priority,
        status=ticket.status,
        category=ticket.category,
        sub_category=ticket.sub_category,
        tracking_number=ticket.tracking_number,
        case_type=ticket.case_type,
        issue_summary=ticket.issue_summary,
        customer_request=ticket.customer_request,
        source_chat_id=ticket.source_chat_id,
        required_action=ticket.required_action,
        missing_fields=ticket.missing_fields,
        last_customer_message=ticket.last_customer_message,
        customer_update=ticket.customer_update,
        resolution_summary=ticket.resolution_summary,
        last_human_update=ticket.last_human_update,
        requested_time=ticket.requested_time,
        destination=ticket.destination,
        preferred_reply_channel=ticket.preferred_reply_channel,
        preferred_reply_contact=ticket.preferred_reply_contact,
        market_id=ticket.market_id,
        market_code=ticket.market.code if getattr(ticket, "market", None) else None,
        country_code=ticket.country_code,
        conversation_state=ticket.conversation_state,
        customer=CustomerRead.model_validate(ticket.customer) if ticket.customer else None,
        assignee=UserRead.model_validate(ticket.assignee) if ticket.assignee else None,
        team=TeamRead.model_validate(ticket.team) if ticket.team else None,
        tags=[TagRead.model_validate(tag) for tag in tag_rows],
        ai_summary=ticket.ai_summary,
        ai_classification=ticket.ai_classification,
        ai_confidence=ticket.ai_confidence,
        first_response_at=ticket.first_response_at,
        first_response_due_at=ticket.first_response_due_at,
        resolution_due_at=ticket.resolution_due_at,
        first_response_breached=ticket.first_response_breached,
        resolution_breached=ticket.resolution_breached,
        reopen_count=ticket.reopen_count,
        resolution_category=ticket.resolution_category,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        comments=[CommentRead.model_validate(x) for x in ticket.comments],
        internal_notes=[InternalNoteRead.model_validate(x) for x in ticket.internal_notes],
        attachments=[AttachmentRead.model_validate(x) for x in ticket.attachments],
        outbound_messages=[OutboundMessageRead.model_validate(x) for x in ticket.outbound_messages],
        ai_intakes=[AIIntakeRead.model_validate(x) for x in ticket.ai_intakes],
        active_market_bulletins=[
            MarketBulletinRead.model_validate(x)
            for x in list_active_bulletins(
                db,
                market_id=ticket.market_id,
                country_code=ticket.country_code,
                channel=ticket.preferred_reply_channel or (ticket.source_channel.value if ticket.source_channel else None),
            )
        ],
    )


@router.post("", response_model=TicketRead)
def create_ticket_endpoint(payload: TicketCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    with managed_session(db):
        ticket = create_ticket(db, payload, current_user)
        db.flush()
    return _serialize_ticket(ticket, db)


@router.get("", response_model=list[TicketListItem])
def list_tickets_endpoint(
    q: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    assignee_id: int | None = None,
    team_id: int | None = None,
    overdue: bool | None = None,
    limit: int = 50,
    skip: int = 0,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    tickets = list_tickets(
        db,
        current_user,
        q=q,
        status_value=status,
        priority_value=priority,
        assignee_id=assignee_id,
        team_id=team_id,
        overdue=overdue,
        limit=limit,
        skip=skip,
    )
    result = []
    for t in tickets:
        result.append(
            TicketListItem(
                id=t.id,
                ticket_no=t.ticket_no,
                title=t.title,
                status=t.status,
                priority=t.priority,
                source_channel=t.source_channel,
                category=t.category,
                sub_category=t.sub_category,
                tracking_number=t.tracking_number,
                customer_name=t.customer.name if t.customer else None,
                assignee_name=t.assignee.display_name if t.assignee else None,
                team_name=t.team.name if t.team else None,
                updated_at=t.updated_at,
                resolution_due_at=t.resolution_due_at,
                overdue=compute_sla_snapshot(t).get("overdue", False),
            )
        )
    return result


@router.get("/{ticket_id}", response_model=TicketRead)
def get_ticket_endpoint(ticket_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_visible(current_user, ticket, db)
    return _serialize_ticket(ticket, db)


@router.patch("/{ticket_id}", response_model=TicketRead)
def update_ticket_endpoint(ticket_id: int, payload: TicketUpdate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    with managed_session(db):
        ticket = update_ticket(db, ticket_id, payload, current_user)
        db.flush()
    return _serialize_ticket(ticket, db)


@router.post("/{ticket_id}/assign", response_model=TicketRead)
def assign_ticket_endpoint(ticket_id: int, payload: TicketAssignRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    with managed_session(db):
        ticket = assign_ticket(db, ticket_id, payload, current_user)
        db.flush()
    return _serialize_ticket(ticket, db)


@router.post("/{ticket_id}/status", response_model=TicketRead)
def change_status_endpoint(ticket_id: int, payload: TicketStatusChangeRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    with managed_session(db):
        ticket = change_status(db, ticket_id, payload, current_user)
        db.flush()
    return _serialize_ticket(ticket, db)


@router.post("/{ticket_id}/escalate", response_model=TicketRead)
def escalate_ticket_endpoint(ticket_id: int, payload: TicketEscalateRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    with managed_session(db):
        ticket = escalate_ticket(db, ticket_id, payload, current_user)
        db.flush()
    return _serialize_ticket(ticket, db)


@router.post("/{ticket_id}/reopen", response_model=TicketRead)
def reopen_ticket_endpoint(ticket_id: int, payload: TicketReopenRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    with managed_session(db):
        ticket = reopen_ticket(db, ticket_id, payload, current_user)
        db.flush()
    return _serialize_ticket(ticket, db)


@router.post("/{ticket_id}/comments", response_model=CommentRead)
def add_comment_endpoint(ticket_id: int, payload: CommentCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    with managed_session(db):
        row = add_comment(db, ticket_id, payload, current_user)
        db.flush()
    return row


@router.post("/{ticket_id}/internal-notes", response_model=InternalNoteRead)
def add_internal_note_endpoint(ticket_id: int, payload: InternalNoteCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    with managed_session(db):
        row = add_internal_note(db, ticket_id, payload, current_user)
        db.flush()
    return row


@router.post("/{ticket_id}/attachments", response_model=AttachmentRead)
def upload_attachment_endpoint(
    ticket_id: int,
    file: UploadFile = File(...),
    visibility: str = Form("external"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    with managed_session(db):
        item = add_attachment(
            db,
            ticket_id,
            file,
            NoteVisibility(visibility),
            current_user,
        )
        db.flush()
    return item


@router.get("/{ticket_id}/outbound/channels/capabilities")
def ticket_outbound_channel_capabilities(ticket_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_visible(current_user, ticket, db)
    return {
        "channels": [item.to_dict() for item in list_outbound_channel_capabilities(db=db, ticket=ticket)],
    }


@router.post("/{ticket_id}/outbound/draft", response_model=OutboundMessageRead)
def save_draft_endpoint(ticket_id: int, payload: OutboundDraftCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    with managed_session(db):
        row = save_outbound_draft(db, ticket_id, payload, current_user)
        db.flush()
    return row


@router.post("/{ticket_id}/outbound/send")
def send_message_endpoint(ticket_id: int, payload: OutboundSendRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    with managed_session(db):
        ticket = get_ticket_or_404(db, ticket_id)
        ensure_ticket_visible(current_user, ticket, db)
        require_outbound_channel_sendable(db, ticket=ticket, channel=payload.channel)
        row = send_outbound_message(db, ticket_id, payload, current_user)
        db.flush()
    return _serialize_outbound_message(row)


@router.post("/{ticket_id}/email/inbound", response_model=InboundEmailIngestResponse)
def ingest_inbound_email_endpoint(ticket_id: int, payload: InboundEmailIngestRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    with managed_session(db):
        result = ingest_ticket_inbound_email(db, ticket_id=ticket_id, payload=payload, current_user=current_user)
        db.flush()
    return InboundEmailIngestResponse(
        ok=True,
        created=result.created,
        message=InboundEmailMessageRead.model_validate(result.row),
        ticket_event_id=result.row.ticket_event_id,
        audit_id=result.row.audit_id,
    )


@router.post("/{ticket_id}/email/outbound/{message_id}/delivery-receipt", response_model=EmailDeliveryReceiptResponse)
def record_email_delivery_receipt_endpoint(ticket_id: int, message_id: int, payload: EmailDeliveryReceiptRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    with managed_session(db):
        result = record_email_delivery_receipt(db, ticket_id=ticket_id, message_id=message_id, payload=payload, current_user=current_user)
        db.flush()
    message = result.message
    return EmailDeliveryReceiptResponse(
        ok=True,
        created=result.created,
        message_id=message.id,
        ticket_id=message.ticket_id,
        status=message.status,
        provider_status=message.provider_status,
        delivery_status=message.delivery_status or payload.delivery_status,
        delivery_event_type=message.delivery_event_type,
        delivery_receipt_provider=message.delivery_receipt_provider,
        delivery_receipt_id=message.delivery_receipt_id,
        delivery_receipt_at=message.delivery_receipt_at,
        delivery_detail=message.delivery_detail,
        failure_code=message.failure_code,
        failure_reason=message.failure_reason,
        ticket_event_id=result.ticket_event_id,
        audit_id=result.audit_id,
    )


@router.post("/{ticket_id}/ai-intakes", response_model=AIIntakeRead)
def add_ai_intake_endpoint(ticket_id: int, payload: AIIntakeCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    with managed_session(db):
        row = add_ai_intake(db, ticket_id, payload, current_user)
        db.flush()
    return row


@router.get("/{ticket_id}/ai-intakes", response_model=list[AIIntakeRead])
def list_ai_intakes(ticket_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_visible(current_user, ticket, db)
    return [AIIntakeRead.model_validate(x) for x in ticket.ai_intakes]


@router.get("/{ticket_id}/timeline-legacy", response_model=list[TimelineItemRead], deprecated=True)
def get_ticket_timeline(ticket_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_visible(current_user, ticket, db)
    items = build_unified_timeline(db, ticket_id)
    return [TimelineItemRead(**item) for item in items]


@router.get("/{ticket_id}/events")
def get_ticket_events_endpoint(ticket_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    events = get_ticket_events(db, ticket_id, current_user)
    return [
        {
            "id": event.id,
            "event_type": event.event_type.value,
            "field_name": event.field_name,
            "old_value": event.old_value,
            "new_value": event.new_value,
            "note": event.note,
            "created_at": format_utc(event.created_at),
        }
        for event in events
    ]
