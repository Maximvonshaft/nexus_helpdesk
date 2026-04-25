from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from ..db import get_db
from ..enums import SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole
from ..models import Customer, IntegrationClient, Market, Team, Ticket, User
from ..schemas import CustomerInput, TicketCreate
from ..services.integration_auth import (
    AuthenticatedIntegrationClient,
    authenticate_integration_client,
    enforce_rate_limit,
    get_idempotent_response,
    record_integration_response,
    require_scope,
    stable_request_hash,
    error_code_from_status,
)
from ..services.ticket_service import create_ticket
from ..settings import get_settings
from ..unit_of_work import managed_session
from ..utils.normalize import normalize_email, normalize_phone
from ..utils.time import utc_now

router = APIRouter(prefix='/api/v1/integration', tags=['integration'])
settings = get_settings()
TERMINAL_STATUSES = {TicketStatus.resolved, TicketStatus.closed, TicketStatus.canceled}


class IntegrationTaskRequest(BaseModel):
    contact_id: str
    channel: str = 'whatsapp'
    summary: str
    tracking_number: Optional[str] = None
    priority: str = 'normal'
    description: Optional[str] = None
    metadata: Optional[dict] = None
    country_code: Optional[str] = None
    market_code: Optional[str] = None


def get_authenticated_integration_client(
    db: Session = Depends(get_db),
    x_client_key_id: str | None = Header(default=None, alias='X-Client-Key-Id'),
    x_client_key: str | None = Header(default=None, alias='X-Client-Key'),
    x_api_key: str | None = Header(default=None, alias='X-API-Key'),
) -> AuthenticatedIntegrationClient:
    return authenticate_integration_client(
        db,
        x_client_key_id=x_client_key_id,
        x_client_key=x_client_key,
        x_api_key=x_api_key,
    )


def _normalize_channel(channel: str | None) -> SourceChannel:
    if not channel:
        return SourceChannel.whatsapp
    value = channel.lower().strip()
    if value == 'whatsapp':
        return SourceChannel.whatsapp
    if value == 'email':
        return SourceChannel.email
    if value in {'web', 'web_chat', 'chat'}:
        return SourceChannel.web_chat
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unsupported integration channel: {channel}")


def _contact_match_filters(contact_id: str):
    cleaned = (contact_id or '').strip()
    phone_norm = normalize_phone(cleaned)
    email_norm = normalize_email(cleaned)
    filters = [
        Ticket.preferred_reply_contact == cleaned,
        Ticket.source_chat_id == cleaned,
        Ticket.customer.has(Customer.phone == cleaned),
        Ticket.customer.has(Customer.email == cleaned),
        Ticket.customer.has(Customer.external_ref == cleaned),
    ]
    if phone_norm:
        filters.extend([
            Ticket.preferred_reply_contact == phone_norm,
            Ticket.source_chat_id == phone_norm,
            Ticket.customer.has(Customer.phone_normalized == phone_norm),
        ])
    if email_norm:
        filters.append(Ticket.customer.has(Customer.email_normalized == email_norm))
    return filters


def _customer_contact_filters(contact_id: str):
    cleaned = (contact_id or '').strip()
    phone_norm = normalize_phone(cleaned)
    email_norm = normalize_email(cleaned)
    filters = [Customer.phone == cleaned, Customer.email == cleaned, Customer.external_ref == cleaned]
    if phone_norm:
        filters.append(Customer.phone_normalized == phone_norm)
    if email_norm:
        filters.append(Customer.email_normalized == email_norm)
    return filters


def _ticket_duplicate_contact_filters(contact_id: str):
    cleaned = (contact_id or '').strip()
    phone_norm = normalize_phone(cleaned)
    filters = [Ticket.preferred_reply_contact == cleaned, Ticket.source_chat_id == cleaned]
    if phone_norm and phone_norm != cleaned:
        filters.extend([Ticket.preferred_reply_contact == phone_norm, Ticket.source_chat_id == phone_norm])
    return filters


def _normalize_priority(priority: str | None) -> TicketPriority:
    if not priority:
        return TicketPriority.medium
    value = priority.lower().strip()
    mapping = {
        'low': TicketPriority.low,
        'medium': TicketPriority.medium,
        'normal': TicketPriority.medium,
        'high': TicketPriority.high,
        'urgent': TicketPriority.urgent,
        'critical': TicketPriority.urgent,
    }
    return mapping.get(value, TicketPriority.medium)


def _pick_actor(db: Session) -> User:
    actor = (
        db.query(User)
        .filter(User.is_active.is_(True), User.role.in_([UserRole.lead, UserRole.admin, UserRole.manager]))
        .order_by(User.id.asc())
        .first()
    )
    if actor:
        return actor
    actor = db.query(User).filter(User.is_active.is_(True)).order_by(User.id.asc()).first()
    if actor:
        return actor
    raise RuntimeError('No active user available to own integration-created tickets')


def _resolve_market(db: Session, *, country_code: str | None = None, market_code: str | None = None) -> Optional[Market]:
    q = db.query(Market).filter(Market.is_active.is_(True))
    if market_code:
        market = q.filter(Market.code == market_code.upper()).first()
        if market:
            return market
    if country_code:
        market = q.filter(Market.country_code == country_code.upper()).order_by(Market.id.asc()).first()
        if market:
            return market
    return None


def _pick_support_team(db: Session, *, country_code: str | None = None, market: Optional[Market] = None) -> Optional[Team]:
    q = db.query(Team).filter(Team.is_active.is_(True))
    if market is not None:
        team = q.filter(Team.market_id == market.id).order_by(Team.id.asc()).first()
        if team:
            return team
    if country_code:
        team = q.join(Team.market).filter_by(country_code=country_code.upper()).order_by(Team.id.asc()).first()
        if team:
            return team
    return (
        db.query(Team)
        .filter(Team.is_active.is_(True), or_(Team.team_type == 'support', Team.name.ilike('%support%')))
        .order_by(Team.id.asc())
        .first()
    )


def _ticket_brief(ticket: Ticket) -> dict:
    return {
        'id': ticket.id,
        'case_ref': ticket.ticket_no,
        'title': ticket.title,
        'status': ticket.status.value if hasattr(ticket.status, 'value') else str(ticket.status),
        'priority': ticket.priority.value if hasattr(ticket.priority, 'value') else str(ticket.priority),
        'tracking_number': ticket.tracking_number,
        'team': ticket.team.name if ticket.team else None,
        'assignee': ticket.assignee.display_name if ticket.assignee else None,
        'updated_at': ticket.updated_at.isoformat() if isinstance(ticket.updated_at, datetime) else None,
    }


def _integration_error_payload(exc: HTTPException) -> dict:
    detail = exc.detail
    if isinstance(detail, dict):
        return {'ok': False, **detail}
    return {'ok': False, 'detail': detail}


def _record_integration_error(
    db: Session,
    *,
    client: AuthenticatedIntegrationClient,
    endpoint: str,
    method: str,
    idempotency_key: str | None,
    request_hash: str | None,
    exc: HTTPException,
) -> None:
    if client.client_id is not None:
        row = db.query(IntegrationClient).filter(IntegrationClient.id == client.client_id).first()
        if row is not None:
            row.last_used_at = utc_now()
    record_integration_response(
        db,
        client=client,
        endpoint=endpoint,
        method=method,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        status_code=exc.status_code,
        response_payload=_integration_error_payload(exc),
        error_code=error_code_from_status(exc.status_code),
    )
    db.commit()


@router.get('/profile/{contact_id}')
def nexusdesk_customer_profile(
    contact_id: str,
    channel: str = Query(default='whatsapp'),
    db: Session = Depends(get_db),
    client: AuthenticatedIntegrationClient = Depends(get_authenticated_integration_client),
):
    try:
        with managed_session(db):
            require_scope(client, 'profile.read')
            enforce_rate_limit(db, client, 'integration.profile')
            normalized_channel = _normalize_channel(channel)

            tickets = (
                db.query(Ticket)
                .options(joinedload(Ticket.customer), joinedload(Ticket.team), joinedload(Ticket.assignee))
                .filter(or_(*_contact_match_filters(contact_id)))
                .order_by(Ticket.updated_at.desc())
                .limit(20)
                .all()
            )

            customer = db.query(Customer).filter(or_(*_customer_contact_filters(contact_id))).first()
            if not customer and tickets:
                customer = tickets[0].customer

            if not customer and not tickets:
                response = {'ok': True, 'found': False, 'message': 'No customer profile found for this contact.', 'channel': normalized_channel.value}
                record_integration_response(db, client=client, endpoint='integration.profile', method='GET', idempotency_key=None, request_hash=None, status_code=200, response_payload=response)
                db.flush()
                return response

            active_tasks = [_ticket_brief(ticket) for ticket in tickets if ticket.status not in TERMINAL_STATUSES]
            dispute_history = [_ticket_brief(ticket) for ticket in tickets]
            response = {
                'ok': True,
                'found': True,
                'channel': normalized_channel.value,
                'customer': {
                    'id': customer.id if customer else None,
                    'name': customer.name if customer else None,
                    'phone': customer.phone if customer else contact_id,
                    'email': customer.email if customer else None,
                    'external_ref': customer.external_ref if customer else None,
                },
                'active_tasks': active_tasks,
                'dispute_history': dispute_history,
            }
            record_integration_response(db, client=client, endpoint='integration.profile', method='GET', idempotency_key=None, request_hash=None, status_code=200, response_payload=response)
            db.flush()
            return response
    except HTTPException as exc:
        if client.client_id is not None or client.is_legacy:
            _record_integration_error(
                db,
                client=client,
                endpoint='integration.profile',
                method='GET',
                idempotency_key=None,
                request_hash=None,
                exc=exc,
            )
        raise


@router.post('/task')
def nexusdesk_escalate_task(
    payload: IntegrationTaskRequest,
    request: Request,
    db: Session = Depends(get_db),
    client: AuthenticatedIntegrationClient = Depends(get_authenticated_integration_client),
    idempotency_key: str | None = Header(default=None, alias='Idempotency-Key'),
):
    request_hash = stable_request_hash(payload.model_dump())
    try:
        with managed_session(db):
            require_scope(client, 'task.write')
            enforce_rate_limit(db, client, 'integration.task')
            if settings.integration_require_idempotency_key and not idempotency_key:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Idempotency-Key is required for integration writes')

            if idempotency_key:
                existing_response = get_idempotent_response(db, client, 'integration.task', idempotency_key, request_hash)
                if existing_response is not None:
                    return existing_response

            actor = _pick_actor(db)
            market = _resolve_market(db, country_code=payload.country_code, market_code=payload.market_code)
            team = _pick_support_team(db, country_code=payload.country_code, market=market)

            filters = [
                or_(*_ticket_duplicate_contact_filters(payload.contact_id)),
                Ticket.status.notin_(list(TERMINAL_STATUSES)),
            ]
            if payload.tracking_number:
                filters.append(Ticket.tracking_number == payload.tracking_number)

            existing = db.query(Ticket).filter(*filters).order_by(Ticket.updated_at.desc()).first()
            if existing:
                response = {
                    'ok': True,
                    'case_ref': existing.ticket_no,
                    'status': 'existing',
                    'message': 'Matching open task already exists in Resolution Center.',
                }
                record_integration_response(db, client=client, endpoint='integration.task', method='POST', idempotency_key=idempotency_key, request_hash=request_hash, status_code=200, response_payload=response)
                db.flush()
                return response

            channel = _normalize_channel(payload.channel)
            priority = _normalize_priority(payload.priority)
            description = payload.description or payload.summary
            metadata = payload.metadata or {}
            summary_text = f"{payload.summary} {description}"

            ticket = create_ticket(
                db,
                TicketCreate(
                    title=payload.summary[:255],
                    description=description,
                    source=TicketSource.api,
                    source_channel=channel,
                    priority=priority,
                    tracking_number=payload.tracking_number,
                    team_id=team.id if team else actor.team_id,
                    market_id=market.id if market else None,
                    country_code=payload.country_code or (market.country_code if market else None),
                    customer=CustomerInput(name=payload.contact_id, phone=payload.contact_id),
                    case_type='Complaint Escalation' if '投诉' in summary_text else 'Manual Escalation',
                    issue_summary=payload.summary,
                    customer_request=description,
                    source_chat_id=payload.contact_id,
                    required_action='Manual review and follow-up with customer',
                    last_customer_message=description,
                    customer_update='Case created and queued for manual handling.',
                    last_human_update='Created by NexusDesk integration endpoint',
                    preferred_reply_channel=channel.value,
                    preferred_reply_contact=payload.contact_id,
                    ai_summary=f"Escalated by {metadata.get('source')}" if metadata.get('source') else None,
                    ai_classification='manual_escalation',
                ),
                actor,
            )

            response = {
                'ok': True,
                'case_ref': ticket.ticket_no,
                'status': 'created',
                'message': 'Task escalated to Resolution Center successfully.',
            }
            record_integration_response(db, client=client, endpoint='integration.task', method='POST', idempotency_key=idempotency_key, request_hash=request_hash, status_code=200, response_payload=response)
            db.flush()
            return response
    except HTTPException as exc:
        if client.client_id is not None or client.is_legacy:
            _record_integration_error(
                db,
                client=client,
                endpoint='integration.task',
                method='POST',
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                exc=exc,
            )
        raise
