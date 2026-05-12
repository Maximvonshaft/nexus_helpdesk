from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..enums import EventType, MessageStatus
from ..models import TicketOutboundMessage
from ..services.audit_service import log_event
from ..services.outbound_semantics import validate_customer_outbound_channel
from ..services.permissions import ensure_can_send_outbound, ensure_ticket_visible
from ..services.reply_channel_policy import ReplyTargetError, resolve_ticket_reply_target
from ..services.ticket_service import get_ticket_or_404
from ..unit_of_work import managed_session
from .deps import get_current_user

router = APIRouter(prefix='/api/tickets', tags=['outbound-approval'])


@router.post('/{ticket_id}/outbound/{message_id}/approve')
def approve_outbound_draft(ticket_id: int, message_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    with managed_session(db):
        ticket = get_ticket_or_404(db, ticket_id)
        ensure_ticket_visible(current_user, ticket, db)
        ensure_can_send_outbound(current_user, db)
        message = db.query(TicketOutboundMessage).filter(TicketOutboundMessage.id == message_id, TicketOutboundMessage.ticket_id == ticket.id).first()
        if message is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Outbound message not found')
        if message.status != MessageStatus.draft:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='only_draft_outbound_can_be_approved')
        try:
            validate_customer_outbound_channel(message.channel)
            reply_target = resolve_ticket_reply_target(ticket)
        except (ValueError, ReplyTargetError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        if message.channel != reply_target.channel:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='draft_channel_does_not_match_ticket_reply_target')
        message.status = MessageStatus.pending
        message.provider_status = 'approved_pending_dispatch'
        message.error_message = None
        message.failure_code = None
        message.failure_reason = None
        message.locked_at = None
        message.locked_by = None
        message.next_retry_at = None
        message.retry_count = 0
        log_event(db, ticket_id=ticket.id, actor_id=current_user.id, event_type=EventType.outbound_queued, note='draft_approved', payload={'channel': message.channel.value, 'outbound_message_id': message.id, 'provider_status': message.provider_status, 'actor_id': current_user.id})
        log_event(db, ticket_id=ticket.id, actor_id=current_user.id, event_type=EventType.outbound_queued, note='outbound_queued', payload={'channel': message.channel.value, 'outbound_message_id': message.id, 'provider_status': message.provider_status, 'actor_id': current_user.id})
        db.flush()
        return {'id': message.id, 'ticket_id': message.ticket_id, 'channel': message.channel.value, 'status': message.status.value, 'provider_status': message.provider_status}
