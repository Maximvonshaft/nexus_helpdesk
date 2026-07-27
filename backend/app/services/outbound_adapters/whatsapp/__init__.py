from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy.orm import Session

from ...enums import MessageStatus, SourceChannel
from ...models import ChannelAccount, Ticket, TicketOutboundMessage
from ...models_whatsapp import WhatsAppConnection
from ...utils.time import utc_now
from ..secret_crypto import SecretCryptoService
from ..whatsapp_baileys_sidecar import (
    BaileysSidecarError,
    send_baileys_text,
)
from ..whatsapp_meta_cloud import (
    MetaCloudTransportError,
    send_meta_cloud_text,
)
from ..whatsapp_runtime_settings import get_whatsapp_runtime_settings


@dataclass(frozen=True)
class WhatsAppRoute:
    channel_account_id: int
    connection_id: int
    account_id: str
    transport: str
    target: str
    source: str

    def context(self, *, idempotency_key: str) -> dict[str, Any]:
        value = asdict(self)
        value.update(
            {
                "adapter": "whatsapp",
                "channel": SourceChannel.whatsapp.value,
                "idempotency_key": idempotency_key,
            }
        )
        return value


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _target(ticket: Ticket | None) -> tuple[str | None, str]:
    if ticket is None:
        return None, "ticket_missing"
    customer = ticket.customer
    for source, value in (
        ("ticket.source_chat_id", ticket.source_chat_id),
        ("ticket.preferred_reply_contact", ticket.preferred_reply_contact),
        ("customer.phone", getattr(customer, "phone", None)),
    ):
        cleaned = _clean(value)
        if cleaned:
            return cleaned, source
    return None, "whatsapp_target_missing"


def _ready_connection_query(db: Session):
    return (
        db.query(WhatsAppConnection)
        .join(
            ChannelAccount,
            ChannelAccount.id == WhatsAppConnection.channel_account_id,
        )
        .filter(
            ChannelAccount.provider == SourceChannel.whatsapp.value,
            ChannelAccount.is_active.is_(True),
            WhatsAppConnection.desired_state == "active",
            WhatsAppConnection.observed_state == "connected",
            WhatsAppConnection.authentication_state == "linked",
            WhatsAppConnection.listener_state == "active",
            WhatsAppConnection.verification_state == "verified",
            WhatsAppConnection.observed_generation
            == WhatsAppConnection.desired_generation,
        )
    )


def resolve_whatsapp_route(
    db: Session,
    *,
    message: TicketOutboundMessage,
    ticket: Ticket | None,
) -> WhatsAppRoute:
    if message.channel != SourceChannel.whatsapp:
        raise ValueError("whatsapp_adapter_received_non_whatsapp_message")
    if ticket is None or ticket.tenant_id is None:
        raise ValueError("whatsapp_ticket_tenant_missing")
    target, target_source = _target(ticket)
    if not target:
        raise ValueError("whatsapp_target_missing")

    query = _ready_connection_query(db).filter(
        WhatsAppConnection.tenant_id == ticket.tenant_id,
    )
    connection = None
    account_source = ""
    if ticket.channel_account_id:
        connection = query.filter(
            WhatsAppConnection.channel_account_id
            == ticket.channel_account_id,
        ).first()
        account_source = "ticket.channel_account_id"
    if connection is None and ticket.market_id is not None:
        connection = (
            query.filter(ChannelAccount.market_id == ticket.market_id)
            .order_by(ChannelAccount.priority.asc(), ChannelAccount.id.asc())
            .first()
        )
        account_source = "ticket.market_id"
    if connection is None:
        connection = (
            query.filter(ChannelAccount.market_id.is_(None))
            .order_by(ChannelAccount.priority.asc(), ChannelAccount.id.asc())
            .first()
        )
        account_source = "tenant_global"
    if connection is None:
        raise ValueError("verified_whatsapp_connection_missing")
    account = connection.channel_account
    return WhatsAppRoute(
        channel_account_id=account.id,
        connection_id=connection.id,
        account_id=account.account_id,
        transport=connection.transport,
        target=target,
        source=f"{target_source}:{account_source}",
    )


def dispatch_whatsapp_outbound(
    db: Session,
    *,
    message: TicketOutboundMessage,
    ticket: Ticket | None,
    idempotency_key: str,
) -> tuple[MessageStatus, str | None, object | None, dict[str, Any]]:
    settings = get_whatsapp_runtime_settings()
    if not settings.enabled:
        return _failed(
            "whatsapp_disabled",
            "WHATSAPP_ENABLED=false blocks WhatsApp dispatch",
            {
                "adapter": "whatsapp",
                "channel": SourceChannel.whatsapp.value,
                "idempotency_key": idempotency_key,
            },
            retryable=False,
        )
    try:
        route = resolve_whatsapp_route(
            db,
            message=message,
            ticket=ticket,
        )
    except ValueError as exc:
        return _failed(
            str(exc),
            str(exc),
            {
                "adapter": "whatsapp",
                "channel": SourceChannel.whatsapp.value,
                "idempotency_key": idempotency_key,
            },
            retryable=str(exc) == "verified_whatsapp_connection_missing",
        )

    connection = (
        db.query(WhatsAppConnection)
        .join(
            ChannelAccount,
            ChannelAccount.id == WhatsAppConnection.channel_account_id,
        )
        .filter(
            WhatsAppConnection.id == route.connection_id,
            ChannelAccount.provider == SourceChannel.whatsapp.value,
        )
        .first()
    )
    if connection is None:
        return _failed(
            "whatsapp_connection_not_found",
            "WhatsApp connection disappeared during route resolution",
            route.context(idempotency_key=idempotency_key),
            retryable=True,
        )
    context = route.context(idempotency_key=idempotency_key)
    try:
        if route.transport == "baileys_sidecar":
            result = send_baileys_text(
                connection,
                target=route.target,
                body=message.body,
                idempotency_key=idempotency_key,
                metadata={
                    "tenant_id": ticket.tenant_id if ticket else None,
                    "ticket_id": message.ticket_id,
                    "outbound_message_id": message.id,
                    "connection_id": connection.id,
                },
            )
            provider_status = "whatsapp_baileys_sent"
        elif route.transport == "meta_cloud_api":
            access_token = SecretCryptoService.whatsapp().decrypt(
                connection.access_token_encrypted
            )
            if not access_token:
                raise ValueError("meta_access_token_missing")
            result = send_meta_cloud_text(
                connection,
                access_token=access_token,
                target=route.target,
                body=message.body,
            )
            provider_status = "whatsapp_meta_sent"
        else:
            raise ValueError("unsupported_whatsapp_transport")
    except (BaileysSidecarError, MetaCloudTransportError) as exc:
        return _failed(
            exc.code,
            exc.message,
            context,
            retryable=exc.retryable,
        )
    except (RuntimeError, ValueError) as exc:
        return _failed(
            str(exc),
            str(exc),
            context,
            retryable=False,
        )

    context["provider_message_id"] = result.provider_message_id
    context["transport"] = route.transport
    connection.last_outbound_at = result.sent_at
    connection.updated_at = utc_now()
    return MessageStatus.sent, provider_status, result.sent_at, context


def _failed(
    failure_code: str,
    message: str,
    context: dict[str, Any],
    *,
    retryable: bool,
) -> tuple[MessageStatus, str | None, object | None, dict[str, Any]]:
    safe = dict(context)
    safe.update(
        {
            "failure_code": failure_code[:120],
            "error": message[:1000],
            "retryable": retryable,
        }
    )
    return MessageStatus.failed, failure_code[:120], None, safe
