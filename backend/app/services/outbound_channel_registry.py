from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..enums import SourceChannel
from ..models import ChannelAccount, Ticket
from ..settings import get_settings


EXTERNAL_READY_CANDIDATE_CHANNELS = frozenset({
    SourceChannel.whatsapp.value,
    SourceChannel.telegram.value,
    SourceChannel.sms.value,
})

EXTERNAL_EXPERIMENTAL_CHANNELS = frozenset({
    SourceChannel.email.value,
})

LOCAL_CHANNELS = frozenset({SourceChannel.web_chat.value})
NOT_CUSTOMER_SENDABLE_CHANNELS = frozenset({SourceChannel.internal.value})

E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")


@dataclass(frozen=True)
class OutboundChannelCapability:
    channel: str
    label: str
    dispatch_type: str
    status: str
    customer_sendable: bool
    enabled: bool
    configured: bool
    account_required: bool
    target_required: bool
    supports_send: bool
    supports_inbound_sync: bool
    supports_delivery_receipt: bool
    supports_attachments: bool
    external_send: bool
    target_validation: str | None = None
    missing: list[str] = field(default_factory=list)
    operator_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _value(raw: Any) -> str:
    value = getattr(raw, "value", raw)
    return str(value or "").strip().lower()


def _label(channel: str) -> str:
    return {
        SourceChannel.whatsapp.value: "WhatsApp",
        SourceChannel.telegram.value: "Telegram",
        SourceChannel.sms.value: "SMS",
        SourceChannel.email.value: "Email",
        SourceChannel.web_chat.value: "WebChat",
        SourceChannel.internal.value: "Internal",
    }.get(channel, channel)


def _target_validation(channel: str) -> str | None:
    return {
        SourceChannel.whatsapp.value: "whatsapp_contact_or_session",
        SourceChannel.telegram.value: "telegram_chat_or_session",
        SourceChannel.sms.value: "e164_phone",
        SourceChannel.email.value: "email_address_with_subject",
        SourceChannel.web_chat.value: "linked_webchat_conversation",
    }.get(channel)


def _has_matching_channel_account(db: Session | None, *, channel: str, ticket: Ticket | None = None) -> bool:
    if db is None:
        return False
    query = db.query(ChannelAccount).filter(
        ChannelAccount.provider == channel,
        ChannelAccount.is_active.is_(True),
    )
    if ticket is not None:
        if getattr(ticket, "channel_account_id", None):
            if query.filter(ChannelAccount.id == ticket.channel_account_id).first() is not None:
                return True
        if getattr(ticket, "market_id", None) is not None:
            if query.filter(ChannelAccount.market_id == ticket.market_id).first() is not None:
                return True
    return query.filter(ChannelAccount.market_id.is_(None)).first() is not None


def _has_webchat_conversation(db: Session | None, *, ticket: Ticket | None = None) -> bool:
    if db is None or ticket is None:
        return False
    try:
        from ..webchat_models import WebchatConversation
    except Exception:
        return False
    return db.query(WebchatConversation.id).filter(WebchatConversation.ticket_id == ticket.id).first() is not None


def _ticket_target(ticket: Ticket | None, *, channel: str) -> str | None:
    if ticket is None:
        return None
    customer = getattr(ticket, "customer", None)
    values: list[str | None]
    if channel == SourceChannel.email.value:
        values = [ticket.preferred_reply_contact, ticket.source_chat_id, getattr(customer, "email", None)]
    elif channel == SourceChannel.sms.value:
        values = [ticket.preferred_reply_contact, ticket.source_chat_id, getattr(customer, "phone", None)]
    else:
        values = [ticket.source_chat_id, ticket.preferred_reply_contact, getattr(customer, "phone", None)]
    for value in values:
        cleaned = str(value or "").strip()
        if cleaned:
            return cleaned
    return None


def is_valid_e164_phone(value: str | None) -> bool:
    return bool(value and E164_RE.match(value.strip()))


def _target_ready(ticket: Ticket | None, *, channel: str) -> bool:
    target = _ticket_target(ticket, channel=channel)
    if not target:
        return False
    if channel == SourceChannel.sms.value:
        return is_valid_e164_phone(target)
    return True


def get_outbound_channel_capability(
    channel: SourceChannel | str,
    *,
    db: Session | None = None,
    ticket: Ticket | None = None,
) -> OutboundChannelCapability:
    channel_value = _value(channel)
    settings = get_settings()
    missing: list[str] = []

    if channel_value in NOT_CUSTOMER_SENDABLE_CHANNELS:
        return OutboundChannelCapability(
            channel=channel_value,
            label=_label(channel_value),
            dispatch_type="internal",
            status="not_customer_sendable",
            customer_sendable=False,
            enabled=False,
            configured=False,
            account_required=False,
            target_required=False,
            supports_send=False,
            supports_inbound_sync=False,
            supports_delivery_receipt=False,
            supports_attachments=False,
            external_send=False,
            target_validation=None,
            missing=["internal_is_not_an_outbound_customer_channel"],
            operator_note="Use internal notes or system events instead of outbound send.",
        )

    if channel_value in LOCAL_CHANNELS:
        configured = _has_webchat_conversation(db, ticket=ticket) if ticket is not None else True
        if ticket is not None and not configured:
            missing.append("linked_webchat_conversation")
        return OutboundChannelCapability(
            channel=channel_value,
            label=_label(channel_value),
            dispatch_type="local",
            status="local_ready" if configured else "not_ready",
            customer_sendable=True,
            enabled=configured,
            configured=configured,
            account_required=False,
            target_required=True,
            supports_send=configured,
            supports_inbound_sync=True,
            supports_delivery_receipt=False,
            supports_attachments=False,
            external_send=False,
            target_validation=_target_validation(channel_value),
            missing=missing,
            operator_note="Local WebChat delivery only; no external provider dispatch should occur.",
        )

    if channel_value in EXTERNAL_EXPERIMENTAL_CHANNELS:
        return OutboundChannelCapability(
            channel=channel_value,
            label=_label(channel_value),
            dispatch_type="external",
            status="experimental_not_ready",
            customer_sendable=False,
            enabled=False,
            configured=False,
            account_required=True,
            target_required=True,
            supports_send=False,
            supports_inbound_sync=False,
            supports_delivery_receipt=False,
            supports_attachments=False,
            external_send=True,
            target_validation=_target_validation(channel_value),
            missing=["email_account_registry", "email_send_schema", "email_provider_adapter"],
            operator_note="Email exists in the enum/outbox layer but is blocked until account, schema, and adapter closure are implemented.",
        )

    if channel_value in EXTERNAL_READY_CANDIDATE_CHANNELS:
        account_configured = _has_matching_channel_account(db, channel=channel_value, ticket=ticket) if db is not None else False
        target_configured = _target_ready(ticket, channel=channel_value) if ticket is not None else True
        if not bool(settings.enable_outbound_dispatch):
            missing.append("enable_outbound_dispatch")
        if settings.outbound_provider != "openclaw":
            missing.append("outbound_provider_openclaw")
        if not account_configured:
            missing.append(f"{channel_value}_channel_account")
        if not target_configured:
            missing.append(f"valid_{_target_validation(channel_value)}")
        status_value = "ready" if not missing else "configurable"
        return OutboundChannelCapability(
            channel=channel_value,
            label=_label(channel_value),
            dispatch_type="external",
            status=status_value,
            customer_sendable=True,
            enabled=not missing,
            configured=account_configured and target_configured,
            account_required=True,
            target_required=True,
            supports_send=not missing,
            supports_inbound_sync=True,
            supports_delivery_receipt=False,
            supports_attachments=False,
            external_send=True,
            target_validation=_target_validation(channel_value),
            missing=missing,
            operator_note="External provider dispatch is allowed only when runtime, account, and target gates are closed.",
        )

    return OutboundChannelCapability(
        channel=channel_value,
        label=_label(channel_value),
        dispatch_type="unknown",
        status="not_ready",
        customer_sendable=False,
        enabled=False,
        configured=False,
        account_required=False,
        target_required=False,
        supports_send=False,
        supports_inbound_sync=False,
        supports_delivery_receipt=False,
        supports_attachments=False,
        external_send=False,
        missing=["unsupported_channel"],
        operator_note="Unsupported outbound channel.",
    )


def list_outbound_channel_capabilities(
    *,
    db: Session | None = None,
    ticket: Ticket | None = None,
) -> list[OutboundChannelCapability]:
    return [
        get_outbound_channel_capability(channel, db=db, ticket=ticket)
        for channel in SourceChannel
    ]


def require_outbound_channel_sendable(db: Session, *, ticket: Ticket, channel: SourceChannel | str) -> OutboundChannelCapability:
    capability = get_outbound_channel_capability(channel, db=db, ticket=ticket)
    if not capability.customer_sendable:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": "outbound_channel_not_customer_sendable",
                "channel": capability.channel,
                "status": capability.status,
                "missing": capability.missing,
            },
        )
    if not capability.supports_send:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": "outbound_channel_not_ready",
                "channel": capability.channel,
                "status": capability.status,
                "missing": capability.missing,
            },
        )
    return capability
