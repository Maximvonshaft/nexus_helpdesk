from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from ..db import get_db
from ..enums import SourceChannel, TicketPriority, TicketSource, TicketStatus
from ..models import Customer, Market, Team, Tenant, Ticket, User
from ..schemas import CustomerInput, TicketCreate
from ..services.integration_auth import (
    AuthenticatedIntegrationClient,
    authenticate_integration_client,
    begin_integration_idempotency,
    enforce_rate_limit,
    error_code_from_status,
    record_integration_response,
    require_principal_scope,
    stable_request_hash,
)
from ..services.permissions import CAP_TICKET_ASSIGN, resolve_capabilities
from ..services.ticket_service import create_ticket
from ..settings import get_settings
from ..unit_of_work import managed_session
from ..utils.normalize import normalize_email, normalize_phone

router = APIRouter(prefix="/api/v1/integration", tags=["integration"])
settings = get_settings()
TERMINAL_STATUSES = {
    TicketStatus.resolved,
    TicketStatus.closed,
    TicketStatus.canceled,
}


class IntegrationTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contact_id: str = Field(min_length=1, max_length=320)
    channel: str = Field(default="whatsapp", max_length=40)
    summary: str = Field(min_length=1, max_length=255)
    tracking_number: Optional[str] = Field(default=None, max_length=120)
    priority: str = Field(default="normal", max_length=24)
    description: Optional[str] = Field(default=None, max_length=4000)
    metadata: Optional[dict] = None
    country_code: Optional[str] = Field(default=None, max_length=8)
    market_code: Optional[str] = Field(default=None, max_length=16)
    tenant_key: Optional[str] = Field(default=None, max_length=80)


def get_authenticated_integration_client(
    db: Session = Depends(get_db),
    x_client_key_id: str | None = Header(default=None, alias="X-Client-Key-Id"),
    x_client_key: str | None = Header(default=None, alias="X-Client-Key"),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> AuthenticatedIntegrationClient:
    return authenticate_integration_client(
        db,
        x_client_key_id=x_client_key_id,
        x_client_key=x_client_key,
        x_api_key=x_api_key,
    )


def _normalize_channel(channel: str | None) -> SourceChannel:
    value = str(channel or "whatsapp").strip().lower()
    if value == "whatsapp":
        return SourceChannel.whatsapp
    if value == "email":
        return SourceChannel.email
    if value in {"web", "web_chat", "chat"}:
        return SourceChannel.web_chat
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Unsupported integration channel: {channel}",
    )


def _normalized_tenant_key(value: str | None) -> str | None:
    cleaned = str(value or "").strip().lower()
    return cleaned or None


def _target_tenant(
    db: Session,
    *,
    client: AuthenticatedIntegrationClient,
    requested_tenant_key: str | None,
) -> Tenant:
    requested = _normalized_tenant_key(requested_tenant_key)
    if client.scope_type == "tenant":
        if client.tenant_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="integration_client_tenant_scope_invalid",
            )
        tenant = db.get(Tenant, int(client.tenant_id))
        if tenant is None or not tenant.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="integration_client_tenant_unavailable",
            )
        if requested is not None and requested != tenant.tenant_key:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="integration_target_tenant_conflict",
            )
        return tenant

    if client.scope_type != "platform":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="integration_client_scope_invalid",
        )
    if requested is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="platform_integration_target_tenant_required",
        )
    tenant = (
        db.query(Tenant)
        .filter(
            Tenant.tenant_key == requested,
            Tenant.is_active.is_(True),
        )
        .first()
    )
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="integration_target_tenant_not_found",
        )
    return tenant


def _contact_match_filters(contact_id: str):
    cleaned = str(contact_id or "").strip()
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
        filters.extend(
            [
                Ticket.preferred_reply_contact == phone_norm,
                Ticket.source_chat_id == phone_norm,
                Ticket.customer.has(Customer.phone_normalized == phone_norm),
            ]
        )
    if email_norm:
        filters.append(
            Ticket.customer.has(Customer.email_normalized == email_norm)
        )
    return filters


def _customer_contact_filters(contact_id: str):
    cleaned = str(contact_id or "").strip()
    phone_norm = normalize_phone(cleaned)
    email_norm = normalize_email(cleaned)
    filters = [
        Customer.phone == cleaned,
        Customer.email == cleaned,
        Customer.external_ref == cleaned,
    ]
    if phone_norm:
        filters.append(Customer.phone_normalized == phone_norm)
    if email_norm:
        filters.append(Customer.email_normalized == email_norm)
    return filters


def _ticket_duplicate_contact_filters(contact_id: str):
    cleaned = str(contact_id or "").strip()
    phone_norm = normalize_phone(cleaned)
    email_norm = normalize_email(cleaned)
    filters = [
        Ticket.preferred_reply_contact == cleaned,
        Ticket.source_chat_id == cleaned,
        Ticket.customer.has(Customer.external_ref == cleaned),
    ]
    if phone_norm:
        filters.extend(
            [
                Ticket.preferred_reply_contact == phone_norm,
                Ticket.source_chat_id == phone_norm,
                Ticket.customer.has(Customer.phone_normalized == phone_norm),
            ]
        )
    if email_norm:
        filters.append(
            Ticket.customer.has(Customer.email_normalized == email_norm)
        )
    return filters


def _normalize_priority(priority: str | None) -> TicketPriority:
    value = str(priority or "normal").strip().lower()
    return {
        "low": TicketPriority.low,
        "medium": TicketPriority.medium,
        "normal": TicketPriority.medium,
        "high": TicketPriority.high,
        "urgent": TicketPriority.urgent,
        "critical": TicketPriority.urgent,
    }.get(value, TicketPriority.medium)


def _pick_actor(db: Session, *, tenant_id: int) -> User:
    candidates = (
        db.query(User)
        .filter(
            User.tenant_id == tenant_id,
            User.is_active.is_(True),
        )
        .order_by(User.id.asc())
        .all()
    )
    for actor in candidates:
        if CAP_TICKET_ASSIGN in resolve_capabilities(actor, db):
            return actor
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="integration_target_tenant_has_no_ticket_assign_actor",
    )


def _resolve_market(
    db: Session,
    *,
    tenant_id: int,
    country_code: str | None = None,
    market_code: str | None = None,
) -> Optional[Market]:
    query = db.query(Market).filter(
        Market.tenant_id == tenant_id,
        Market.is_active.is_(True),
    )
    if market_code:
        market = query.filter(Market.code == market_code.strip().upper()).first()
        if market is not None:
            return market
    if country_code:
        return (
            query.filter(Market.country_code == country_code.strip().upper())
            .order_by(Market.id.asc())
            .first()
        )
    return None


def _pick_support_team(
    db: Session,
    *,
    tenant_id: int,
    country_code: str | None = None,
    market: Optional[Market] = None,
) -> Optional[Team]:
    query = db.query(Team).filter(
        Team.tenant_id == tenant_id,
        Team.is_active.is_(True),
    )
    if market is not None:
        team = (
            query.filter(Team.market_id == market.id)
            .order_by(Team.id.asc())
            .first()
        )
        if team is not None:
            return team
    if country_code:
        team = (
            query.join(Market, Market.id == Team.market_id)
            .filter(
                Market.tenant_id == tenant_id,
                Market.country_code == country_code.strip().upper(),
            )
            .order_by(Team.id.asc())
            .first()
        )
        if team is not None:
            return team
    return (
        query.filter(
            or_(
                Team.team_type == "support",
                Team.name.ilike("%support%"),
            )
        )
        .order_by(Team.id.asc())
        .first()
    )


def _ticket_brief(ticket: Ticket) -> dict:
    return {
        "id": ticket.id,
        "case_ref": ticket.ticket_no,
        "title": ticket.title,
        "status": (
            ticket.status.value
            if hasattr(ticket.status, "value")
            else str(ticket.status)
        ),
        "priority": (
            ticket.priority.value
            if hasattr(ticket.priority, "value")
            else str(ticket.priority)
        ),
        "tracking_number": ticket.tracking_number,
        "team": ticket.team.name if ticket.team else None,
        "assignee": ticket.assignee.display_name if ticket.assignee else None,
        "updated_at": (
            ticket.updated_at.isoformat()
            if isinstance(ticket.updated_at, datetime)
            else None
        ),
    }


def _customer_input(contact_id: str) -> CustomerInput:
    cleaned = str(contact_id or "").strip()
    email = normalize_email(cleaned)
    phone = normalize_phone(cleaned)
    return CustomerInput(
        name=cleaned,
        email=email if "@" in cleaned else None,
        phone=phone if phone and "@" not in cleaned else None,
        external_ref=(
            cleaned
            if not email and not phone
            else None
        ),
    )


def _integration_error_payload(exc: HTTPException) -> dict:
    detail = exc.detail
    if isinstance(detail, dict):
        code = detail.get("code") or detail.get("error_code")
        return {
            "ok": False,
            "error_code": str(code or "request_failed")[:120],
        }
    return {
        "ok": False,
        "error_code": error_code_from_status(exc.status_code),
        "detail": str(detail)[:200],
    }


def _record_integration_error(
    db: Session,
    *,
    client: AuthenticatedIntegrationClient,
    endpoint: str,
    method: str,
    idempotency_key: str | None,
    request_hash: str | None,
    target_tenant_id: int | None,
    exc: HTTPException,
) -> None:
    # Platform requests that fail before resolving a target Tenant are rejected
    # without manufacturing an unowned customer-affecting log envelope.
    if target_tenant_id is None:
        return
    record_integration_response(
        db,
        client=client,
        endpoint=endpoint,
        method=method,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        status_code=exc.status_code,
        response_payload=_integration_error_payload(exc),
        target_tenant_id=target_tenant_id,
        error_code=error_code_from_status(exc.status_code),
    )
    db.commit()


def _idempotency_begin_response(
    kind: str,
    response_json: dict | None = None,
    error_code: str | None = None,
) -> JSONResponse | dict | None:
    if kind == "owner":
        return None
    if kind == "replay":
        payload = dict(response_json or {})
        payload.pop("schema", None)
        payload["idempotent"] = True
        return payload
    if kind == "processing":
        return JSONResponse(
            {
                "ok": False,
                "error_code": "request_processing",
                "retry_after_ms": 1500,
            },
            status_code=202,
        )
    if kind == "conflict":
        return JSONResponse(
            {
                "ok": False,
                "error_code": error_code
                or "idempotency_key_reused_with_different_payload",
            },
            status_code=409,
        )
    return JSONResponse(
        {"ok": False, "error_code": error_code or "request_failed"},
        status_code=409,
    )


@router.get("/profile/{contact_id}")
def nexusdesk_customer_profile(
    contact_id: str,
    channel: str = Query(default="whatsapp"),
    tenant_key: str | None = Query(default=None),
    db: Session = Depends(get_db),
    client: AuthenticatedIntegrationClient = Depends(
        get_authenticated_integration_client
    ),
):
    target_tenant_id: int | None = client.tenant_id
    try:
        with managed_session(db):
            require_principal_scope(
                client,
                tenant_scope="profile.read",
                platform_scope="platform.profile.read",
            )
            tenant = _target_tenant(
                db,
                client=client,
                requested_tenant_key=tenant_key,
            )
            target_tenant_id = int(tenant.id)
            enforce_rate_limit(db, client, "integration.profile")
            normalized_channel = _normalize_channel(channel)

            tickets = (
                db.query(Ticket)
                .options(
                    joinedload(Ticket.customer),
                    joinedload(Ticket.team),
                    joinedload(Ticket.assignee),
                )
                .filter(
                    Ticket.tenant_id == tenant.id,
                    or_(*_contact_match_filters(contact_id)),
                )
                .order_by(Ticket.updated_at.desc(), Ticket.id.desc())
                .limit(20)
                .all()
            )
            customer = (
                db.query(Customer)
                .filter(
                    Customer.tenant_id == tenant.id,
                    or_(*_customer_contact_filters(contact_id)),
                )
                .order_by(Customer.id.asc())
                .first()
            )
            if customer is None and tickets:
                customer = tickets[0].customer

            if customer is None and not tickets:
                response = {
                    "ok": True,
                    "found": False,
                    "message": "No customer profile found for this contact.",
                    "channel": normalized_channel.value,
                }
            else:
                response = {
                    "ok": True,
                    "found": True,
                    "channel": normalized_channel.value,
                    "customer": {
                        "id": customer.id if customer else None,
                        "name": customer.name if customer else None,
                        "phone": customer.phone if customer else None,
                        "email": customer.email if customer else None,
                        "external_ref": customer.external_ref if customer else None,
                    },
                    "active_tasks": [
                        _ticket_brief(ticket)
                        for ticket in tickets
                        if ticket.status not in TERMINAL_STATUSES
                    ],
                    "dispute_history": [
                        _ticket_brief(ticket) for ticket in tickets
                    ],
                }

            record_integration_response(
                db,
                client=client,
                endpoint="integration.profile",
                method="GET",
                idempotency_key=None,
                request_hash=None,
                status_code=200,
                response_payload=response,
                target_tenant_id=target_tenant_id,
            )
            db.flush()
            return response
    except HTTPException as exc:
        _record_integration_error(
            db,
            client=client,
            endpoint="integration.profile",
            method="GET",
            idempotency_key=None,
            request_hash=None,
            target_tenant_id=target_tenant_id,
            exc=exc,
        )
        raise


@router.post("/task")
def nexusdesk_escalate_task(
    payload: IntegrationTaskRequest,
    request: Request,
    db: Session = Depends(get_db),
    client: AuthenticatedIntegrationClient = Depends(
        get_authenticated_integration_client
    ),
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
    ),
):
    request_hash = stable_request_hash(payload.model_dump())
    target_tenant_id: int | None = client.tenant_id
    try:
        with managed_session(db):
            require_principal_scope(
                client,
                tenant_scope="task.write",
                platform_scope="platform.task.write",
            )
            tenant = _target_tenant(
                db,
                client=client,
                requested_tenant_key=payload.tenant_key,
            )
            target_tenant_id = int(tenant.id)
            enforce_rate_limit(db, client, "integration.task")
            if settings.integration_require_idempotency_key and not idempotency_key:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Idempotency-Key is required for integration writes",
                )

            if idempotency_key:
                begin = begin_integration_idempotency(
                    db,
                    client=client,
                    endpoint="integration.task",
                    method="POST",
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    target_tenant_id=target_tenant_id,
                )
                begin_response = _idempotency_begin_response(
                    begin.kind,
                    begin.response_json,
                    begin.error_code,
                )
                if begin_response is not None:
                    return begin_response

            actor = _pick_actor(db, tenant_id=target_tenant_id)
            market = _resolve_market(
                db,
                tenant_id=target_tenant_id,
                country_code=payload.country_code,
                market_code=payload.market_code,
            )
            team = _pick_support_team(
                db,
                tenant_id=target_tenant_id,
                country_code=payload.country_code,
                market=market,
            )

            filters = [
                Ticket.tenant_id == target_tenant_id,
                or_(*_ticket_duplicate_contact_filters(payload.contact_id)),
                Ticket.status.notin_(list(TERMINAL_STATUSES)),
            ]
            if payload.tracking_number:
                filters.append(
                    Ticket.tracking_number == payload.tracking_number.strip()
                )
            existing = (
                db.query(Ticket)
                .filter(*filters)
                .order_by(Ticket.updated_at.desc(), Ticket.id.desc())
                .first()
            )
            if existing is not None:
                response = {
                    "ok": True,
                    "case_ref": existing.ticket_no,
                    "status": "existing",
                    "message": (
                        "Matching open task already exists in Resolution Center."
                    ),
                }
            else:
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
                        tracking_number=(
                            payload.tracking_number.strip()
                            if payload.tracking_number
                            else None
                        ),
                        team_id=team.id if team else actor.team_id,
                        market_id=market.id if market else None,
                        country_code=(
                            payload.country_code.strip().upper()
                            if payload.country_code
                            else market.country_code if market else None
                        ),
                        customer=_customer_input(payload.contact_id),
                        case_type=(
                            "Complaint Escalation"
                            if "投诉" in summary_text
                            else "Manual Escalation"
                        ),
                        issue_summary=payload.summary,
                        customer_request=description,
                        source_chat_id=payload.contact_id.strip(),
                        required_action=(
                            "Manual review and follow-up with customer"
                        ),
                        last_customer_message=description,
                        customer_update=(
                            "Case created and queued for manual handling."
                        ),
                        last_human_update=(
                            "Created by NexusDesk integration endpoint"
                        ),
                        preferred_reply_channel=channel.value,
                        preferred_reply_contact=payload.contact_id.strip(),
                        ai_summary=(
                            f"Escalated by {metadata.get('source')}"
                            if metadata.get("source")
                            else None
                        ),
                        ai_classification="manual_escalation",
                    ),
                    actor,
                )
                if ticket.tenant_id != target_tenant_id:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="integration_created_ticket_tenant_conflict",
                    )
                response = {
                    "ok": True,
                    "case_ref": ticket.ticket_no,
                    "status": "created",
                    "message": (
                        "Task escalated to Resolution Center successfully."
                    ),
                }

            record_integration_response(
                db,
                client=client,
                endpoint="integration.task",
                method="POST",
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                status_code=200,
                response_payload=response,
                target_tenant_id=target_tenant_id,
            )
            db.flush()
            return response
    except HTTPException as exc:
        _record_integration_error(
            db,
            client=client,
            endpoint="integration.task",
            method="POST",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            target_tenant_id=target_tenant_id,
            exc=exc,
        )
        raise
