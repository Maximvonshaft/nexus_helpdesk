from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from ..db import get_db
from ..enums import NoteVisibility, SourceChannel
from ..models import ExternalChannelTranscriptMessage, Ticket, Tag, TicketTag, TicketOutboundMessage
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
    MarketRead,
    ExternalChannelAttachmentReferenceRead,
    ExternalChannelConversationRead,
    ExternalChannelTranscriptRead,
    TeamRead,
    CustomerRead,
    TagRead,
    MarketBulletinRead,
)
from ..services.canonical_ticket_service import (
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
from ..utils.time import ensure_utc, format_utc, utc_now
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
    return {
        "id": row.id,
        "ticket_id": row.ticket_id,
        "channel": channel_value,
        "channel_ui_label": outbound_ui_label(channel_value),
        "subject": row.subject,
        "body": row.body,
        "recipients": row.recipients,
        "status": row.status.value if hasattr(row.status, "value") else str(row.status),
        "external_message_id": row.external_message_id,
        "error_message": row.error_message,
        "created_at": row.created_at,
        "sent_at": row.sent_at,
        "updated_at": row.updated_at,
        "provider_status": row.provider_status,
        "delivery_status": row.delivery_status,
        "mailbox_thread_id": row.mailbox_thread_id,
        "mailbox_message_id": row.mailbox_message_id,
        "mailbox_references": row.mailbox_references,
        "from_address": row.from_address,
        "reply_to_address": row.reply_to_address,
        "provider_message_id": row.provider_message_id,
        "failure_reason": row.failure_reason,
        "outbound_is_external_send": outbound_is_external_send(channel_value),
        "external_channel_runtime_enabled": bool(settings.external_channel_transport != "disabled"),
    }


def _serialize_ticket(ticket: Ticket, db: Session, current_user) -> TicketRead:
    ensure_ticket_visible(current_user, ticket, db)
    result = TicketRead.model_validate(ticket)
    result.sla = compute_sla_snapshot(ticket)
    result.outbound_messages = [OutboundMessageRead(**_serialize_outbound_message(row)) for row in ticket.outbound_messages]
    result.timeline = [TimelineItemRead(**item) for item in build_unified_timeline(db, ticket, current_user)]
    result.active_bulletins = [MarketBulletinRead.model_validate(row) for row in list_active_bulletins(db, market_id=ticket.market_id, country_code=ticket.country_code)]
    result.outbound_channel_capabilities = list_outbound_channel_capabilities(db, ticket=ticket, current_user=current_user)
    return result


@router.get("", response_model=list[TicketListItem])
def tickets_list(
    q: str | None = None,
    status: str | None = None,
    status_in: str | None = None,
    priority: str | None = None,
    assignee_id: int | None = None,
    team_id: int | None = None,
    overdue: bool | None = None,
    limit: int = 50,
    skip: int = 0,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    status_values = [item.strip() for item in status_in.split(",") if item.strip()] if status_in else None
    rows = list_tickets(
        db,
        current_user,
        q=q,
        status_value=status,
        status_in=status_values,
        priority_value=priority,
        assignee_id=assignee_id,
        team_id=team_id,
        overdue=overdue,
        limit=limit,
        skip=skip,
    )
    return [TicketListItem.model_validate(row) for row in rows]


@router.post("", response_model=TicketRead)
def tickets_create(payload: TicketCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    with managed_session(db):
        ticket = create_ticket(db, payload, current_user)
    return _serialize_ticket(ticket, db, current_user)


@router.get("/{ticket_id}", response_model=TicketRead)
def tickets_get(ticket_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ticket = get_ticket_or_404(db, ticket_id)
    return _serialize_ticket(ticket, db, current_user)


@router.patch("/{ticket_id}", response_model=TicketRead)
def tickets_update(ticket_id: int, payload: TicketUpdate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    with managed_session(db):
        ticket = update_ticket(db, ticket_id, payload, current_user)
    return _serialize_ticket(ticket, db, current_user)


@router.post("/{ticket_id}/assign", response_model=TicketRead)
def tickets_assign(ticket_id: int, payload: TicketAssignRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    with managed_session(db):
        ticket = assign_ticket(db, ticket_id, payload, current_user)
    return _serialize_ticket(ticket, db, current_user)


@router.post("/{ticket_id}/status", response_model=TicketRead)
def tickets_status(ticket_id: int, payload: TicketStatusChangeRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    with managed_session(db):
        ticket = change_status(db, ticket_id, payload, current_user)
    return _serialize_ticket(ticket, db, current_user)


@router.post("/{ticket_id}/escalate", response_model=TicketRead)
def tickets_escalate(ticket_id: int, payload: TicketEscalateRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    with managed_session(db):
        ticket = escalate_ticket(db, ticket_id, payload, current_user)
    return _serialize_ticket(ticket, db, current_user)


@router.post("/{ticket_id}/reopen", response_model=TicketRead)
def tickets_reopen(ticket_id: int, payload: TicketReopenRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    with managed_session(db):
        ticket = reopen_ticket(db, ticket_id, payload, current_user)
    return _serialize_ticket(ticket, db, current_user)


@router.post("/{ticket_id}/comments", response_model=CommentRead)
def tickets_comment(ticket_id: int, payload: CommentCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    with managed_session(db):
        row = add_comment(db, ticket_id, payload, current_user)
    return CommentRead.model_validate(row)


@router.post("/{ticket_id}/internal-notes", response_model=InternalNoteRead)
def tickets_internal_note(ticket_id: int, payload: InternalNoteCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    with managed_session(db):
        row = add_internal_note(db, ticket_id, payload, current_user)
    return InternalNoteRead.model_validate(row)


@router.post("/{ticket_id}/ai-intakes", response_model=AIIntakeRead)
def tickets_ai_intake(ticket_id: int, payload: AIIntakeCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    with managed_session(db):
        row = add_ai_intake(db, ticket_id, payload, current_user)
    return AIIntakeRead.model_validate(row)


@router.post("/{ticket_id}/attachments", response_model=AttachmentRead)
def tickets_attachment(
    ticket_id: int,
    file: UploadFile = File(...),
    visibility: NoteVisibility = Form(default=NoteVisibility.internal),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    with managed_session(db):
        row = add_attachment(db, ticket_id, file, visibility, current_user)
    return AttachmentRead(**_serialize_attachment(row))


@router.post("/{ticket_id}/outbound/drafts", response_model=OutboundMessageRead)
def tickets_outbound_draft(ticket_id: int, payload: OutboundDraftCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    with managed_session(db):
        row = save_outbound_draft(db, ticket_id, payload, current_user)
    return OutboundMessageRead(**_serialize_outbound_message(row))


@router.post("/{ticket_id}/outbound/send", response_model=OutboundMessageRead)
def tickets_outbound_send(ticket_id: int, payload: OutboundSendRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    require_outbound_channel_sendable(payload.channel)
    with managed_session(db):
        row = send_outbound_message(db, ticket_id, payload, current_user)
    return OutboundMessageRead(**_serialize_outbound_message(row))


@router.post("/{ticket_id}/inbound/email", response_model=InboundEmailIngestResponse)
def tickets_inbound_email(ticket_id: int, payload: InboundEmailIngestRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    with managed_session(db):
        result = ingest_ticket_inbound_email(db, ticket_id=ticket_id, payload=payload, actor=current_user)
    return InboundEmailIngestResponse(
        ticket=TicketRead.model_validate(result.ticket),
        message=InboundEmailMessageRead.model_validate(result.message),
        created=result.created,
    )


@router.post("/{ticket_id}/outbound/{message_id}/delivery-receipt", response_model=EmailDeliveryReceiptResponse)
def tickets_email_delivery_receipt(ticket_id: int, message_id: int, payload: EmailDeliveryReceiptRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    with managed_session(db):
        result = record_email_delivery_receipt(db, ticket_id=ticket_id, message_id=message_id, payload=payload, actor=current_user)
    return EmailDeliveryReceiptResponse(message=OutboundMessageRead(**_serialize_outbound_message(result.message)), created=result.created)


@router.get("/{ticket_id}/events")
def tickets_events(ticket_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_visible(current_user, ticket, db)
    return get_ticket_events(db, ticket_id)


@router.get("/{ticket_id}/timeline", response_model=list[TimelineItemRead])
def tickets_timeline(ticket_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_visible(current_user, ticket, db)
    return [TimelineItemRead(**item) for item in build_unified_timeline(db, ticket, current_user)]


@router.get("/{ticket_id}/external-channel/transcript", response_model=ExternalChannelTranscriptRead)
def tickets_external_channel_transcript(ticket_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_visible(current_user, ticket, db)
    settings = get_settings()
    if settings.external_channel_transport != "disabled":
        raise RuntimeError("ExternalChannel runtime must remain disabled")
    messages = (
        db.query(ExternalChannelTranscriptMessage)
        .filter(ExternalChannelTranscriptMessage.ticket_id == ticket.id)
        .order_by(ExternalChannelTranscriptMessage.sent_at.asc(), ExternalChannelTranscriptMessage.id.asc())
        .all()
    )
    conversation = ExternalChannelConversationRead(
        external_channel_conversation_id=ticket.external_channel_conversation_id,
        external_channel_case_id=ticket.external_channel_case_id,
        external_channel_inbox_id=ticket.external_channel_inbox_id,
        external_channel_contact_id=ticket.external_channel_contact_id,
        external_channel_account_id=ticket.external_channel_account_id,
        external_channel_sync_status=ticket.external_channel_sync_status,
        external_channel_last_synced_at=ticket.external_channel_last_synced_at,
    )
    return ExternalChannelTranscriptRead(
        ticket_id=ticket.id,
        conversation=conversation,
        messages=[
            ExternalChannelTranscriptMessageRead(
                id=row.id,
                direction=row.direction,
                message_type=row.message_type,
                body=row.body,
                content_type=row.content_type,
                sender=row.sender,
                recipients=row.recipients,
                sent_at=row.sent_at,
                external_message_id=row.external_message_id,
                metadata_json=row.metadata_json,
                attachments=[ExternalChannelAttachmentReferenceRead.model_validate(item) for item in row.attachment_references],
            )
            for row in messages
        ],
    )
