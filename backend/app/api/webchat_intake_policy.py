from __future__ import annotations

import hashlib
import json
import secrets
from datetime import timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..enums import ConversationState, EventType, NoteVisibility, SourceChannel, TicketPriority, TicketSource, TicketStatus
from ..models import Customer, Ticket, TicketComment, TicketEvent
from ..services.ticket_service import generate_ticket_no
from ..settings import get_settings
from ..unit_of_work import managed_session
from ..utils.normalize import normalize_email
from ..utils.time import utc_now
from ..webchat_models import WebchatCardAction, WebchatConversation, WebchatMessage
from .deps import get_current_user

router = APIRouter(prefix='/api/webchat', tags=['webchat-intake-policy'])
settings = get_settings()

MAX_MESSAGE_CHARS = 2000
WEBCHAT_VISITOR_TOKEN_TTL_DAYS = 7


class WebchatInitRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tenant_key: str = Field(default='default', max_length=120)
    channel_key: str = Field(default='default', max_length=120)
    conversation_id: str | None = Field(default=None, max_length=64)
    visitor_token: str | None = Field(default=None, max_length=160)
    visitor_name: str | None = Field(default=None, max_length=160)
    visitor_email: str | None = Field(default=None, max_length=200)
    visitor_phone: str | None = Field(default=None, max_length=80)
    visitor_ref: str | None = Field(default=None, max_length=160)
    origin: str | None = Field(default=None, max_length=255)
    page_url: str | None = Field(default=None, max_length=700)


class WebchatSendRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    visitor_token: str | None = Field(default=None, min_length=20, max_length=160)
    body: str = Field(min_length=1, max_length=2000)
    client_message_id: str | None = Field(default=None, max_length=120)


class WebchatActionRequest(BaseModel):
    model_config = ConfigDict(extra='ignore')
    visitor_token: str | None = Field(default=None, max_length=160)
    message_id: int | None = None
    card_id: str | None = None
    action_id: str | None = None
    action_type: str | None = None
    payload: dict[str, Any] | None = None


class WebchatReplyRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    body: str = Field(min_length=1, max_length=2000)
    has_fact_evidence: bool = False
    confirm_review: bool = False


class WebchatFastReplyRequest(BaseModel):
    model_config = ConfigDict(extra='ignore')
    session_id: str | None = None
    client_message_id: str | None = None
    body: str | None = None


def _clip(value: str | None, limit: int) -> str | None:
    cleaned = ' '.join(str(value or '').strip().split())
    return cleaned[:limit] if cleaned else None


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _new_public_id() -> str:
    return f"wc_{secrets.token_urlsafe(18).replace('-', '').replace('_', '')[:24]}"


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def _new_token_expiry():
    return utc_now() + timedelta(days=WEBCHAT_VISITOR_TOKEN_TTL_DAYS)


def _ensure_aware_utc(value):
    if value is None:
        return None
    if getattr(value, 'tzinfo', None) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _resolve_visitor_token(header_token: str | None, query_token: str | None, body_token: str | None = None) -> str | None:
    if header_token:
        return header_token
    if getattr(settings, 'webchat_allow_legacy_token_transport', False):
        return body_token or query_token
    return None


def _origin_from_request(request: Request, explicit_origin: str | None = None) -> str | None:
    origin = explicit_origin or request.headers.get('origin')
    if origin:
        return _clip(origin, 255)
    referer = request.headers.get('referer')
    if referer:
        parsed = urlparse(referer)
        if parsed.scheme and parsed.netloc:
            return _clip(f'{parsed.scheme}://{parsed.netloc}', 255)
    return None


def _validate_token(conversation: WebchatConversation, token: str | None) -> None:
    if not token or _hash_token(token) != conversation.visitor_token_hash:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='invalid webchat visitor token')
    expires_at = _ensure_aware_utc(getattr(conversation, 'visitor_token_expires_at', None))
    now = _ensure_aware_utc(utc_now())
    if expires_at is not None and now is not None and expires_at <= now:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='invalid webchat visitor token')


def _body(value: str | None) -> str:
    cleaned = (value or '').strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail='message body is required')
    if len(cleaned) > MAX_MESSAGE_CHARS:
        raise HTTPException(status_code=413, detail=f'message body exceeds {MAX_MESSAGE_CHARS} characters')
    return cleaned


def _message_read(row: WebchatMessage) -> dict[str, Any]:
    return {
        'id': row.id,
        'direction': row.direction,
        'body': row.body,
        'body_text': getattr(row, 'body_text', None) or row.body,
        'message_type': getattr(row, 'message_type', None) or 'text',
        'payload_json': json.loads(row.payload_json) if getattr(row, 'payload_json', None) else None,
        'metadata_json': json.loads(row.metadata_json) if getattr(row, 'metadata_json', None) else None,
        'client_message_id': getattr(row, 'client_message_id', None),
        'delivery_status': getattr(row, 'delivery_status', None) or 'sent',
        'author_label': row.author_label,
        'created_at': row.created_at.isoformat() if row.created_at else None,
    }


def _cors(response: Response, request: Request) -> None:
    origin = request.headers.get('origin')
    if origin:
        response.headers.setdefault('Access-Control-Allow-Origin', origin)
    response.headers.setdefault('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
    response.headers.setdefault('Access-Control-Allow-Headers', 'Content-Type, X-Requested-With, X-Webchat-Visitor-Token')
    response.headers.setdefault('Cache-Control', 'no-store')


@router.options('/{full_path:path}')
def options(full_path: str, request: Request):
    headers = {'Access-Control-Allow-Methods': 'GET, POST, OPTIONS', 'Access-Control-Allow-Headers': 'Content-Type, X-Requested-With, X-Webchat-Visitor-Token', 'Cache-Control': 'no-store'}
    origin = request.headers.get('origin')
    if origin:
        headers['Access-Control-Allow-Origin'] = origin
    return Response(status_code=204, headers=headers)


@router.post('/fast-reply')
def block_fast_reply(payload: WebchatFastReplyRequest, request: Request, response: Response):
    _cors(response, request)
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='webchat_outbound_disabled_intake_only')


@router.post('/init')
def init_webchat(payload: WebchatInitRequest, request: Request, response: Response, db: Session = Depends(get_db), x_webchat_visitor_token: str | None = Header(default=None, alias='X-Webchat-Visitor-Token')):
    _cors(response, request)
    visitor_token = _resolve_visitor_token(x_webchat_visitor_token, None, payload.visitor_token)
    public_id = _clip(payload.conversation_id, 64)
    with managed_session(db):
        if public_id:
            existing = db.query(WebchatConversation).filter(WebchatConversation.public_id == public_id).first()
            if existing:
                _validate_token(existing, visitor_token)
                existing.last_seen_at = utc_now()
                existing.visitor_token_expires_at = _new_token_expiry()
                existing.updated_at = utc_now()
                db.flush()
                return {'conversation_id': existing.public_id, 'visitor_token': visitor_token, 'status': existing.status, 'config': {'poll_interval_ms': 4000, 'max_message_chars': MAX_MESSAGE_CHARS, 'supports_cards': False, 'supports_after_id': True, 'intake_only': True}}

        token = _new_token()
        public_id = _new_public_id()
        visitor_email = _clip(payload.visitor_email, 200)
        visitor_email_norm = normalize_email(visitor_email)
        visitor_name = _clip(payload.visitor_name, 160)
        visitor_phone = _clip(payload.visitor_phone, 80)
        customer = Customer(name=visitor_name or visitor_email or visitor_phone or f'Webchat Visitor {public_id[-6:]}', email=visitor_email, email_normalized=visitor_email_norm, phone=visitor_phone, external_ref=_clip(payload.visitor_ref, 160) or public_id)
        db.add(customer)
        db.flush()
        missing_email = not visitor_email_norm
        ticket = Ticket(
            ticket_no=generate_ticket_no(),
            title=f'Webchat inquiry · {customer.name}',
            description='New webchat intake created from customer website widget.',
            customer_id=customer.id,
            source=TicketSource.user_message,
            source_channel=SourceChannel.web_chat,
            priority=TicketPriority.medium,
            status=TicketStatus.waiting_internal if missing_email else TicketStatus.pending_assignment,
            conversation_state=ConversationState.human_review_required if missing_email else ConversationState.human_owned,
            source_chat_id=public_id,
            preferred_reply_channel=SourceChannel.email.value,
            preferred_reply_contact=visitor_email_norm,
            customer_request='Webchat intake initiated.',
            last_customer_message='Webchat intake initiated.',
            required_action='Customer email required for Webchat intake reply' if missing_email else None,
        )
        db.add(ticket)
        db.flush()
        conversation = WebchatConversation(public_id=public_id, visitor_token_hash=_hash_token(token), visitor_token_expires_at=_new_token_expiry(), tenant_key=_clip(payload.tenant_key, 120) or 'default', channel_key=_clip(payload.channel_key, 120) or 'default', ticket_id=ticket.id, visitor_name=visitor_name, visitor_email=visitor_email, visitor_phone=visitor_phone, visitor_ref=_clip(payload.visitor_ref, 160), origin=_origin_from_request(request, payload.origin), page_url=_clip(payload.page_url, 700), user_agent=_clip(request.headers.get('user-agent'), 300), status='open', last_seen_at=utc_now(), created_at=utc_now(), updated_at=utc_now())
        db.add(conversation)
        db.add(TicketEvent(ticket_id=ticket.id, actor_id=None, event_type=EventType.ticket_created, note='Webchat intake created', payload_json=json.dumps({'public_conversation_id': public_id, 'reply_channel': 'email', 'intake_only': True}, ensure_ascii=False)))
        if missing_email:
            db.add(TicketEvent(ticket_id=ticket.id, actor_id=None, event_type=EventType.internal_note_added, note='customer_email_required_for_webchat_intake', payload_json=json.dumps({'public_conversation_id': public_id, 'intake_only': True}, ensure_ascii=False)))
        db.flush()
        return {'conversation_id': conversation.public_id, 'visitor_token': token, 'status': conversation.status, 'config': {'poll_interval_ms': 4000, 'max_message_chars': MAX_MESSAGE_CHARS, 'supports_cards': False, 'supports_after_id': True, 'intake_only': True}}


@router.post('/conversations/{conversation_id}/messages')
def send_webchat_message(conversation_id: str, payload: WebchatSendRequest, request: Request, response: Response, db: Session = Depends(get_db), x_webchat_visitor_token: str | None = Header(default=None, alias='X-Webchat-Visitor-Token')):
    _cors(response, request)
    visitor_token = _resolve_visitor_token(x_webchat_visitor_token, None, payload.visitor_token)
    with managed_session(db):
        conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == conversation_id).first()
        if not conversation:
            raise HTTPException(status_code=404, detail='webchat conversation not found')
        _validate_token(conversation, visitor_token)
        normalized_body = _body(payload.body)
        existing = None
        if payload.client_message_id:
            existing = db.query(WebchatMessage).filter(WebchatMessage.conversation_id == conversation.id, WebchatMessage.client_message_id == payload.client_message_id, WebchatMessage.direction == 'visitor').first()
        if existing:
            return {'ok': True, 'idempotent': True, 'message': _message_read(existing), 'intake_only': True}
        message = WebchatMessage(conversation_id=conversation.id, ticket_id=conversation.ticket_id, direction='visitor', body=normalized_body, body_text=normalized_body, message_type='text', client_message_id=_clip(payload.client_message_id, 120), delivery_status='sent', metadata_json=json.dumps({'generated_by': 'visitor', 'external_send': False, 'intake_only': True}, ensure_ascii=False), author_label=conversation.visitor_name or 'Visitor')
        db.add(message)
        db.flush()
        ticket = db.query(Ticket).filter(Ticket.id == conversation.ticket_id).first()
        if ticket:
            ticket.last_customer_message = normalized_body
            ticket.customer_request = normalized_body
            ticket.updated_at = utc_now()
            if ticket.status in {TicketStatus.resolved, TicketStatus.closed}:
                ticket.status = TicketStatus.pending_assignment
                ticket.conversation_state = ConversationState.reopened_by_customer
            db.add(TicketComment(ticket_id=ticket.id, author_id=None, body=normalized_body, visibility=NoteVisibility.external))
            db.add(TicketEvent(ticket_id=ticket.id, actor_id=None, event_type=EventType.comment_added, note='Webchat visitor message received', payload_json=json.dumps({'public_conversation_id': conversation_id, 'webchat_message_id': message.id, 'client_message_id': payload.client_message_id, 'intake_only': True}, ensure_ascii=False)))
            if not ticket.preferred_reply_contact:
                db.add(TicketEvent(ticket_id=ticket.id, actor_id=None, event_type=EventType.internal_note_added, note='customer_email_required_for_webchat_intake', payload_json=json.dumps({'public_conversation_id': conversation_id, 'webchat_message_id': message.id, 'intake_only': True}, ensure_ascii=False)))
        conversation.last_seen_at = utc_now()
        conversation.updated_at = utc_now()
        db.flush()
        return {'ok': True, 'message': _message_read(message), 'intake_only': True}


@router.get('/conversations/{conversation_id}/messages')
def poll_webchat_messages(conversation_id: str, request: Request, response: Response, visitor_token: str | None = Query(default=None), after_id: int | None = Query(default=None, ge=0), limit: int = Query(default=50, ge=1, le=100), x_webchat_visitor_token: str | None = Header(default=None, alias='X-Webchat-Visitor-Token'), db: Session = Depends(get_db)):
    _cors(response, request)
    resolved_token = _resolve_visitor_token(x_webchat_visitor_token, visitor_token)
    with managed_session(db):
        conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == conversation_id).first()
        if not conversation:
            raise HTTPException(status_code=404, detail='webchat conversation not found')
        _validate_token(conversation, resolved_token)
        query = db.query(WebchatMessage).filter(WebchatMessage.conversation_id == conversation.id)
        if after_id is not None:
            query = query.filter(WebchatMessage.id > after_id)
        rows = query.order_by(WebchatMessage.id.asc()).limit(limit + 1).all()
        has_more = len(rows) > limit
        rows = rows[:limit]
        return {'conversation_id': conversation.public_id, 'status': conversation.status, 'messages': [_message_read(row) for row in rows], 'has_more': has_more, 'next_after_id': rows[-1].id if rows else after_id, 'intake_only': True}


@router.post('/conversations/{conversation_id}/actions')
def submit_webchat_action(conversation_id: str, payload: WebchatActionRequest, request: Request, response: Response, db: Session = Depends(get_db), x_webchat_visitor_token: str | None = Header(default=None, alias='X-Webchat-Visitor-Token')):
    _cors(response, request)
    visitor_token = _resolve_visitor_token(x_webchat_visitor_token, None, payload.visitor_token)
    with managed_session(db):
        conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == conversation_id).first()
        if not conversation:
            raise HTTPException(status_code=404, detail='webchat conversation not found')
        _validate_token(conversation, visitor_token)
        ticket = db.query(Ticket).filter(Ticket.id == conversation.ticket_id).first()
        action_payload = {'action_id': payload.action_id, 'action_type': payload.action_type, 'card_id': payload.card_id, 'payload': payload.payload or {}, 'intake_only': True}
        action = WebchatCardAction(conversation_id=conversation.id, ticket_id=conversation.ticket_id, message_id=payload.message_id, action_type=payload.action_type or 'action', action_payload_json=json.dumps(action_payload, ensure_ascii=False), submitted_by='visitor', status='submitted', origin=_origin_from_request(request))
        db.add(action)
        db.flush()
        action_text = f"Visitor selected: {payload.action_id or payload.action_type or 'action'}"
        action_message = WebchatMessage(conversation_id=conversation.id, ticket_id=conversation.ticket_id, direction='action', body=action_text, body_text=action_text, message_type='action', payload_json=json.dumps(action_payload, ensure_ascii=False), metadata_json=json.dumps({'generated_by': 'visitor', 'external_send': False, 'intake_only': True}, ensure_ascii=False), delivery_status='sent', action_status='submitted', author_label=conversation.visitor_name or 'Visitor')
        db.add(action_message)
        if ticket:
            if payload.action_type == 'handoff_request' or payload.action_id == 'talk_to_human':
                ticket.required_action = 'WebChat customer requested human support'
                ticket.conversation_state = ConversationState.human_review_required
            db.add(TicketComment(ticket_id=ticket.id, author_id=None, body=action_text, visibility=NoteVisibility.external))
            db.add(TicketEvent(ticket_id=ticket.id, actor_id=None, event_type=EventType.comment_added, note='Webchat card action submitted', payload_json=json.dumps(action_payload, ensure_ascii=False)))
            db.add(TicketEvent(ticket_id=ticket.id, actor_id=None, event_type=EventType.internal_note_added, note='webchat_handoff_ack_suppressed_intake_only', payload_json=json.dumps({'webchat_card_action_id': action.id, 'intake_only': True}, ensure_ascii=False)))
        db.flush()
        return {'ok': True, 'action_id': action.id, 'status': action.status, 'message': _message_read(action_message), 'handoff_triggered': payload.action_type == 'handoff_request' or payload.action_id == 'talk_to_human', 'intake_only': True}


@router.post('/admin/tickets/{ticket_id}/reply')
def reply_webchat(ticket_id: int, payload: WebchatReplyRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='webchat_outbound_disabled_intake_only')
