from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from ..models import ChannelAccount, Customer, Market, Tenant, Ticket
from ..models_agent_routing import ConversationControl
from ..utils.time import utc_now
from ..webchat_models import WebchatConversation, WebchatMessage
from .customer_identity_service import (
    bind_customer_identity,
    resolve_or_create_customer,
)
from .customer_recontact_service import reopen_from_customer_message
from .tenant_authority import tenant_runtime_authority_mode
from .webchat_ai_turn_service import safe_write_webchat_event


class ChannelIntakeError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChannelIntakeContext:
    tenant: Tenant | None
    tenant_id: int | None
    tenant_key: str
    market: Market | None
    customer: Customer
    conversation: WebchatConversation
    control: ConversationControl
    created: bool


@dataclass(frozen=True)
class ChannelIntakeMessage:
    context: ChannelIntakeContext
    message: WebchatMessage
    ticket: Ticket | None
    created: bool


def _account_scope(
    db: Session,
    account: ChannelAccount,
) -> tuple[Tenant | None, int | None, str, Market | None]:
    if not account.is_active:
        raise ChannelIntakeError("channel_account_inactive")
    market = db.get(Market, account.market_id) if account.market_id is not None else None

    if account.tenant_id is None:
        if tenant_runtime_authority_mode() != "shadow":
            raise ChannelIntakeError("channel_account_tenant_missing")
        if market is not None and (not market.is_active or market.tenant_id is not None):
            raise ChannelIntakeError("channel_account_market_tenant_conflict")
        return None, None, "default", market

    tenant = db.get(Tenant, int(account.tenant_id))
    if tenant is None or not tenant.is_active:
        raise ChannelIntakeError("channel_account_tenant_inactive")
    tenant_key = str(tenant.tenant_key or "").strip().lower()
    if not tenant_key or tenant_key == "default":
        raise ChannelIntakeError("channel_account_tenant_key_invalid")
    if market is not None and (
        not market.is_active or market.tenant_id != tenant.id
    ):
        raise ChannelIntakeError("channel_account_market_tenant_conflict")
    return tenant, int(tenant.id), tenant_key, market


def channel_conversation_public_id(
    *,
    tenant_key: str,
    channel_key: str,
    account_id: int,
    external_conversation_key: str,
) -> str:
    digest = hashlib.sha256(
        (
            f"{tenant_key.strip().lower()}:{channel_key.strip().lower()}:"
            f"{account_id}:{external_conversation_key.strip().casefold()}"
        ).encode("utf-8")
    ).hexdigest()[:32]
    prefix = "wa" if channel_key.strip().lower() == "whatsapp" else "ci"
    return f"{prefix}_{digest}"


def _token_hash(
    *,
    tenant_key: str,
    channel_key: str,
    account_id: int,
    external_conversation_key: str,
) -> str:
    return hashlib.sha256(
        (
            f"channel-intake:{tenant_key}:{channel_key}:{account_id}:"
            f"{external_conversation_key}"
        ).encode("utf-8")
    ).hexdigest()


def _control(
    db: Session,
    *,
    conversation: WebchatConversation,
    customer: Customer,
    tenant_key: str,
    market: Market | None,
    channel_key: str,
) -> ConversationControl:
    row = (
        db.query(ConversationControl)
        .filter(ConversationControl.conversation_id == conversation.id)
        .first()
    )
    country_code = str(market.country_code).strip().upper() if market else None
    if row is None:
        row = ConversationControl(
            conversation_id=conversation.id,
            customer_id=customer.id,
            tenant_key=tenant_key,
            country_code=country_code,
            channel_key=channel_key,
            created_at=conversation.created_at or utc_now(),
            updated_at=utc_now(),
        )
        db.add(row)
        db.flush()
        return row
    if (
        row.tenant_key != tenant_key
        or row.channel_key != channel_key
        or row.customer_id not in {None, customer.id}
        or (country_code is not None and row.country_code not in {None, country_code})
    ):
        raise ChannelIntakeError("channel_conversation_scope_conflict")
    row.customer_id = customer.id
    row.country_code = country_code or row.country_code
    row.updated_at = utc_now()
    db.flush()
    return row


def resolve_channel_intake_context(
    db: Session,
    *,
    account: ChannelAccount,
    channel_key: str,
    external_conversation_key: str,
    identity_type: str,
    identity_value: str,
    display_name: str | None,
    visitor_phone: str | None = None,
    visitor_email: str | None = None,
    visitor_ref: str | None = None,
    origin: str,
) -> ChannelIntakeContext:
    tenant, tenant_id, tenant_key, market = _account_scope(db, account)
    normalized_channel = str(channel_key or "").strip().lower()
    external_key = str(external_conversation_key or "").strip()
    if not normalized_channel or not external_key:
        raise ChannelIntakeError("channel_conversation_identity_required")
    if str(account.provider or "").strip().lower() != normalized_channel:
        raise ChannelIntakeError("channel_account_provider_mismatch")

    customer = resolve_or_create_customer(
        db,
        tenant_id=tenant_id,
        identity_type=identity_type,
        identity_value=identity_value,
        display_name=display_name,
        source=normalized_channel,
    )
    secondary = str(visitor_ref or "").strip()
    if secondary:
        bind_customer_identity(
            db,
            customer=customer,
            identity_type="external_ref",
            identity_value=secondary,
            source=normalized_channel,
            display_name=display_name,
        )

    public_id = channel_conversation_public_id(
        tenant_key=tenant_key,
        channel_key=normalized_channel,
        account_id=account.id,
        external_conversation_key=external_key,
    )
    conversation = (
        db.query(WebchatConversation)
        .filter(WebchatConversation.public_id == public_id)
        .first()
    )
    created = conversation is None
    now = utc_now()
    if conversation is None:
        conversation = WebchatConversation(
            public_id=public_id,
            visitor_token_hash=_token_hash(
                tenant_key=tenant_key,
                channel_key=normalized_channel,
                account_id=account.id,
                external_conversation_key=external_key,
            ),
            visitor_token_expires_at=None,
            tenant_key=tenant_key,
            channel_key=normalized_channel,
            ticket_id=None,
            visitor_name=str(display_name or customer.name or "")[:160] or None,
            visitor_email=str(visitor_email or customer.email or "")[:200] or None,
            visitor_phone=str(visitor_phone or customer.phone or "")[:80] or None,
            visitor_ref=str(visitor_ref or external_key)[:160],
            origin=str(origin or normalized_channel)[:255],
            status="open",
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(conversation)
        db.flush()
    else:
        if (
            conversation.tenant_key != tenant_key
            or conversation.channel_key != normalized_channel
        ):
            raise ChannelIntakeError("channel_conversation_scope_conflict")
        conversation.last_seen_at = now
        conversation.updated_at = now
        if visitor_phone and not conversation.visitor_phone:
            conversation.visitor_phone = str(visitor_phone)[:80]
        if visitor_email and not conversation.visitor_email:
            conversation.visitor_email = str(visitor_email)[:200]
        if display_name and not conversation.visitor_name:
            conversation.visitor_name = str(display_name)[:160]

    control = _control(
        db,
        conversation=conversation,
        customer=customer,
        tenant_key=tenant_key,
        market=market,
        channel_key=normalized_channel,
    )
    return ChannelIntakeContext(
        tenant=tenant,
        tenant_id=tenant_id,
        tenant_key=tenant_key,
        market=market,
        customer=customer,
        conversation=conversation,
        control=control,
        created=created,
    )


def append_channel_customer_message(
    db: Session,
    *,
    context: ChannelIntakeContext,
    external_message_id: str,
    body: str,
    created_at: datetime,
    author_label: str,
    metadata: dict[str, object],
) -> ChannelIntakeMessage:
    message_identity = str(external_message_id or "").strip()[:120]
    body_text = str(body or "").strip()
    if not message_identity or not body_text:
        raise ChannelIntakeError("channel_message_identity_and_body_required")
    existing = (
        db.query(WebchatMessage)
        .filter(
            WebchatMessage.conversation_id == context.conversation.id,
            WebchatMessage.client_message_id == message_identity,
        )
        .first()
    )
    ticket = (
        db.get(Ticket, context.conversation.ticket_id)
        if context.conversation.ticket_id is not None
        else None
    )
    if ticket is not None and ticket.tenant_id != context.tenant_id:
        raise ChannelIntakeError("channel_ticket_tenant_conflict")
    if existing is not None:
        return ChannelIntakeMessage(
            context=context,
            message=existing,
            ticket=ticket,
            created=False,
        )

    reopen_from_customer_message(
        db,
        conversation=context.conversation,
        control=context.control,
        ticket=ticket,
        source=context.conversation.channel_key,
        external_message_id=message_identity,
    )
    message = WebchatMessage(
        conversation_id=context.conversation.id,
        ticket_id=ticket.id if ticket is not None else None,
        direction="visitor",
        body=body_text,
        body_text=body_text,
        message_type="text",
        client_message_id=message_identity,
        delivery_status="sent",
        metadata_json=json.dumps(
            {
                "schema": "nexus.channel-intake-message.v1",
                **metadata,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ),
        author_label=str(author_label or "Customer")[:120],
        created_at=created_at,
    )
    db.add(message)
    db.flush()
    context.conversation.last_seen_at = utc_now()
    context.conversation.updated_at = utc_now()
    if ticket is not None:
        ticket.last_customer_message = body_text[:4000]
        if not ticket.customer_request:
            ticket.customer_request = body_text[:4000]
        ticket.preferred_reply_channel = context.conversation.channel_key
        ticket.updated_at = utc_now()
    safe_write_webchat_event(
        db,
        conversation_id=context.conversation.id,
        ticket_id=ticket.id if ticket is not None else None,
        event_type="channel_intake.customer_message_projected",
        payload={
            "channel_key": context.conversation.channel_key,
            "message_id": message.id,
            "external_message_id": message_identity,
            "ticketless": ticket is None,
        },
    )
    return ChannelIntakeMessage(
        context=context,
        message=message,
        ticket=ticket,
        created=True,
    )


__all__ = [
    "ChannelIntakeContext",
    "ChannelIntakeError",
    "ChannelIntakeMessage",
    "append_channel_customer_message",
    "channel_conversation_public_id",
    "resolve_channel_intake_context",
]
