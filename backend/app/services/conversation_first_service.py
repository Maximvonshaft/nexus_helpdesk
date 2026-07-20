from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from ..models import Customer, Tenant, Ticket
from ..models_agent_routing import ConversationControl
from ..utils.time import utc_now
from ..webchat_models import WebchatConversation
from .tenant_authority import stamp_runtime_tenant, tenant_runtime_authority_mode
from .webchat_service import (
    MAX_FIELD_CHARS,
    MAX_MESSAGE_CHARS,
    MAX_URL_CHARS,
    _clip,
    _hash_token,
    _new_public_id,
    _new_token,
    _new_token_expiry,
    _origin_from_request,
    _validate_token,
)
from .webchat_tenant_binding import current_verified_webchat_scope


LOGGER = logging.getLogger("nexusdesk")


def _relational_tenant(db: Session) -> Tenant | None:
    scope = current_verified_webchat_scope(db)
    mode = tenant_runtime_authority_mode()
    if scope is None:
        if mode == "enforce":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="webchat_verified_scope_required",
            )
        return None
    if scope.authority != "server_origin_binding":
        if mode == "enforce":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="webchat_verified_scope_required",
            )
        return None
    tenant = (
        db.query(Tenant)
        .filter(
            Tenant.tenant_key == scope.tenant_key.strip().lower(),
            Tenant.is_active.is_(True),
        )
        .first()
    )
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="webchat_tenant_principal_required",
        )
    return tenant


def ensure_conversation_control(
    db: Session,
    *,
    conversation: WebchatConversation,
    customer_id: int | None = None,
) -> ConversationControl:
    row = (
        db.query(ConversationControl)
        .filter(ConversationControl.conversation_id == conversation.id)
        .first()
    )
    scope = current_verified_webchat_scope(db)
    if row is None:
        row = ConversationControl(
            conversation_id=conversation.id,
            customer_id=customer_id,
            tenant_key=conversation.tenant_key,
            country_code=scope.country_code if scope else None,
            channel_key=conversation.channel_key,
            created_at=conversation.created_at or utc_now(),
            updated_at=utc_now(),
        )
        db.add(row)
    else:
        if customer_id is not None and row.customer_id is None:
            row.customer_id = customer_id
        row.tenant_key = conversation.tenant_key
        row.channel_key = conversation.channel_key
        if scope and scope.country_code:
            row.country_code = scope.country_code
        row.updated_at = utc_now()
    db.flush()
    return row


def _legacy_customer_id(db: Session, conversation: WebchatConversation) -> int | None:
    if conversation.ticket_id is None:
        return None
    ticket = db.get(Ticket, conversation.ticket_id)
    return ticket.customer_id if ticket is not None else None


def _assert_resume_scope(
    db: Session,
    *,
    conversation: WebchatConversation,
    control: ConversationControl,
    tenant: Tenant | None,
) -> None:
    scope = current_verified_webchat_scope(db)
    expected_tenant_id = tenant.id if tenant is not None else None
    customer_id = control.customer_id or _legacy_customer_id(db, conversation)
    customer = db.get(Customer, customer_id) if customer_id is not None else None
    if (
        scope is not None
        and (
            control.tenant_key != scope.tenant_key
            or control.channel_key != scope.channel_key
            or control.country_code != scope.country_code
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="webchat_tenant_relationship_conflict",
        )
    if tenant is not None and (customer is None or customer.tenant_id != expected_tenant_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="webchat_tenant_relationship_conflict",
        )


def create_or_resume_conversation(
    db: Session,
    payload: Any,
    request: Request,
) -> dict[str, Any]:
    scope = current_verified_webchat_scope(db)
    tenant_key = _clip(
        (scope.tenant_key if scope else None)
        or getattr(payload, "tenant_key", None)
        or "default",
        120,
    ) or "default"
    channel_key = _clip(
        (scope.channel_key if scope else None)
        or getattr(payload, "channel_key", None)
        or "default",
        120,
    ) or "default"
    tenant = _relational_tenant(db)
    public_id = _clip(getattr(payload, "conversation_id", None), 64)
    visitor_token = getattr(payload, "visitor_token", None)

    if public_id:
        existing = (
            db.query(WebchatConversation)
            .filter(WebchatConversation.public_id == public_id)
            .first()
        )
        if existing is not None:
            _validate_token(existing, visitor_token)
            control = ensure_conversation_control(
                db,
                conversation=existing,
                customer_id=_legacy_customer_id(db, existing),
            )
            _assert_resume_scope(
                db,
                conversation=existing,
                control=control,
                tenant=tenant,
            )
            existing.last_seen_at = utc_now()
            existing.visitor_token_expires_at = _new_token_expiry()
            existing.updated_at = utc_now()
            existing.page_url = (
                _clip(getattr(payload, "page_url", None), MAX_URL_CHARS)
                or existing.page_url
            )
            existing.origin = (
                _origin_from_request(request, getattr(payload, "origin", None))
                or existing.origin
            )
            existing.user_agent = (
                _clip(request.headers.get("user-agent"), MAX_FIELD_CHARS)
                or existing.user_agent
            )
            db.flush()
            LOGGER.info(
                "webchat_session_resumed",
                extra={
                    "event_payload": {
                        "conversation_id": existing.public_id,
                        "ticket_id": existing.ticket_id,
                    }
                },
            )
            return {
                "conversation_id": existing.public_id,
                "visitor_token": visitor_token,
                "status": existing.status,
                "config": {
                    "poll_interval_ms": 4000,
                    "max_message_chars": MAX_MESSAGE_CHARS,
                    "supports_cards": True,
                    "supports_after_id": True,
                },
            }

    token = _new_token()
    public_id = _new_public_id()
    visitor_name = _clip(getattr(payload, "visitor_name", None), 160)
    visitor_email = _clip(getattr(payload, "visitor_email", None), 200)
    visitor_phone = _clip(getattr(payload, "visitor_phone", None), 80)
    visitor_ref = _clip(getattr(payload, "visitor_ref", None), 160)

    customer = Customer(
        name=visitor_name
        or visitor_email
        or visitor_phone
        or f"Webchat Visitor {public_id[-6:]}",
        email=visitor_email,
        phone=visitor_phone,
        external_ref=visitor_ref or public_id,
    )
    stamp_runtime_tenant(customer, tenant.id if tenant is not None else None)
    db.add(customer)
    db.flush()

    conversation = WebchatConversation(
        public_id=public_id,
        visitor_token_hash=_hash_token(token),
        visitor_token_expires_at=_new_token_expiry(),
        tenant_key=tenant_key,
        channel_key=channel_key,
        ticket_id=None,
        visitor_name=visitor_name,
        visitor_email=visitor_email,
        visitor_phone=visitor_phone,
        visitor_ref=visitor_ref,
        origin=_origin_from_request(request, getattr(payload, "origin", None)),
        page_url=_clip(getattr(payload, "page_url", None), MAX_URL_CHARS),
        user_agent=_clip(request.headers.get("user-agent"), 300),
        status="open",
        last_seen_at=utc_now(),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db.add(conversation)
    db.flush()
    ensure_conversation_control(
        db,
        conversation=conversation,
        customer_id=customer.id,
    )

    LOGGER.info(
        "webchat_session_created",
        extra={
            "event_payload": {
                "conversation_id": public_id,
                "ticket_id": None,
                "origin": conversation.origin,
            }
        },
    )
    return {
        "conversation_id": conversation.public_id,
        "visitor_token": token,
        "status": conversation.status,
        "config": {
            "poll_interval_ms": 4000,
            "max_message_chars": MAX_MESSAGE_CHARS,
            "supports_cards": True,
            "supports_after_id": True,
        },
    }
