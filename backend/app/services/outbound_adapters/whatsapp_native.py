from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Protocol

import httpx
from sqlalchemy.orm import Session

from ...enums import MessageStatus, SourceChannel
from ...models import ChannelAccount, OpenClawConversationLink, Ticket, TicketOutboundMessage
from ...settings import get_settings
from ...utils.time import utc_now

WHATSAPP_NATIVE_DISABLED = "whatsapp_native_disabled"
WHATSAPP_NATIVE_CONFIGURATION_MISSING = "whatsapp_native_configuration_missing"
WHATSAPP_NATIVE_MISSING_ACCOUNT = "missing_whatsapp_channel_account"
WHATSAPP_NATIVE_MISSING_TARGET = "missing_whatsapp_target"
WHATSAPP_NATIVE_TIMEOUT = "whatsapp_native_sidecar_timeout"
WHATSAPP_NATIVE_TRANSPORT_ERROR = "whatsapp_native_sidecar_transport_error"
WHATSAPP_NATIVE_BAD_RESPONSE = "whatsapp_native_sidecar_bad_response"
WHATSAPP_NATIVE_NOT_CONNECTED = "whatsapp_not_connected"


class SidecarClient(Protocol):
    def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any], timeout: float) -> Any:
        ...


class WhatsAppNativeOutboundError(ValueError):
    def __init__(self, failure_code: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(failure_code)
        self.failure_code = failure_code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class WhatsAppNativeRoute:
    account_id: str
    target: str
    chat_jid: str | None
    source: str

    def to_context(self, *, idempotency_key: str) -> dict[str, Any]:
        payload = asdict(self)
        payload["adapter"] = "whatsapp_native_sidecar"
        payload["channel"] = SourceChannel.whatsapp.value
        payload["idempotency_key"] = idempotency_key
        return payload


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _active_account_by_id(db: Session, account_id: str | None) -> ChannelAccount | None:
    cleaned = _clean(account_id)
    if not cleaned:
        return None
    return db.query(ChannelAccount).filter(
        ChannelAccount.account_id == cleaned,
        ChannelAccount.provider == SourceChannel.whatsapp.value,
        ChannelAccount.is_active.is_(True),
    ).first()


def _active_account_by_pk(db: Session, account_pk: int | None) -> ChannelAccount | None:
    if not account_pk:
        return None
    return db.query(ChannelAccount).filter(
        ChannelAccount.id == account_pk,
        ChannelAccount.provider == SourceChannel.whatsapp.value,
        ChannelAccount.is_active.is_(True),
    ).first()


def _active_account_for_market(db: Session, market_id: int | None) -> ChannelAccount | None:
    query = db.query(ChannelAccount).filter(
        ChannelAccount.provider == SourceChannel.whatsapp.value,
        ChannelAccount.is_active.is_(True),
    )
    if market_id is not None:
        row = query.filter(ChannelAccount.market_id == market_id).order_by(ChannelAccount.priority.asc(), ChannelAccount.id.asc()).first()
        if row is not None:
            return row
    return query.filter(ChannelAccount.market_id.is_(None)).order_by(ChannelAccount.priority.asc(), ChannelAccount.id.asc()).first()


def _resolve_account(db: Session, *, ticket: Ticket | None, link: OpenClawConversationLink | None) -> tuple[ChannelAccount | None, str]:
    if ticket is not None:
        row = _active_account_by_pk(db, getattr(ticket, "channel_account_id", None))
        if row is not None:
            return row, "ticket.channel_account_id"
    if link is not None:
        row = _active_account_by_pk(db, getattr(link, "channel_account_id", None))
        if row is not None:
            return row, "openclaw_link.channel_account_id"
        row = _active_account_by_id(db, getattr(link, "account_id", None))
        if row is not None:
            return row, "openclaw_link.account_id"
    if ticket is not None:
        row = _active_account_for_market(db, getattr(ticket, "market_id", None))
        if row is not None:
            return row, "market_or_global_whatsapp_account"
    return None, WHATSAPP_NATIVE_MISSING_ACCOUNT


def _ticket_target(ticket: Ticket | None, link: OpenClawConversationLink | None) -> tuple[str | None, str | None, str]:
    if link is not None:
        recipient = _clean(getattr(link, "recipient", None))
        if recipient:
            return recipient, None, "openclaw_link.recipient"
    if ticket is not None:
        customer = getattr(ticket, "customer", None)
        for source, value in (
            ("ticket.source_chat_id", getattr(ticket, "source_chat_id", None)),
            ("ticket.preferred_reply_contact", getattr(ticket, "preferred_reply_contact", None)),
            ("customer.phone", getattr(customer, "phone", None)),
        ):
            cleaned = _clean(value)
            if cleaned:
                chat_jid = cleaned if cleaned.endswith("@s.whatsapp.net") else None
                return cleaned, chat_jid, source
    return None, None, WHATSAPP_NATIVE_MISSING_TARGET


def resolve_whatsapp_native_route(db: Session, *, message: TicketOutboundMessage, ticket: Ticket | None) -> WhatsAppNativeRoute:
    if message.channel != SourceChannel.whatsapp:
        raise WhatsAppNativeOutboundError(WHATSAPP_NATIVE_CONFIGURATION_MISSING, "Native WhatsApp adapter received a non-WhatsApp message", retryable=False)

    link = ticket.openclaw_link if ticket is not None else None
    target, chat_jid, target_source = _ticket_target(ticket, link)
    if target is None:
        raise WhatsAppNativeOutboundError(WHATSAPP_NATIVE_MISSING_TARGET, "No WhatsApp target address is available", retryable=False)

    account, account_source = _resolve_account(db, ticket=ticket, link=link)
    if account is None:
        raise WhatsAppNativeOutboundError(WHATSAPP_NATIVE_MISSING_ACCOUNT, "No active WhatsApp channel account is configured", retryable=True)

    return WhatsAppNativeRoute(
        account_id=account.account_id,
        target=target,
        chat_jid=chat_jid,
        source=f"{target_source}:{account_source}",
    )


def _failed(
    failure_code: str,
    error_message: str,
    context: dict[str, Any],
    *,
    retryable: bool,
) -> tuple[MessageStatus, str | None, object | None, dict[str, Any]]:
    safe_context = dict(context)
    safe_context["failure_code"] = failure_code
    safe_context["error"] = error_message[:500]
    safe_context["retryable"] = retryable
    return MessageStatus.failed, failure_code, None, safe_context


def _parse_sent_at(value: Any):
    if value is None:
        return utc_now()
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return utc_now()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return utc_now()


def _sidecar_send(
    *,
    route: WhatsAppNativeRoute,
    body: str,
    idempotency_key: str,
    metadata: dict[str, Any],
    client: SidecarClient | None = None,
) -> tuple[MessageStatus, str | None, object | None, dict[str, Any]]:
    settings = get_settings()
    context = route.to_context(idempotency_key=idempotency_key)
    context["metadata"] = metadata
    if not settings.whatsapp_native_enabled:
        return _failed(WHATSAPP_NATIVE_DISABLED, "Native WhatsApp dispatch is disabled", context, retryable=False)
    if not settings.whatsapp_sidecar_token:
        return _failed(WHATSAPP_NATIVE_CONFIGURATION_MISSING, "Native WhatsApp sidecar token is not configured", context, retryable=False)

    payload = {
        "idempotency_key": idempotency_key,
        "target": route.target,
        "chat_jid": route.chat_jid,
        "body": body,
        "reply_to_message_id": None,
        "metadata": metadata,
    }
    url = f"{settings.whatsapp_sidecar_url}/accounts/{route.account_id}/send"
    active_client = client or httpx.Client()
    close_client = client is None
    try:
        response = active_client.post(
            url,
            headers={"Authorization": f"Bearer {settings.whatsapp_sidecar_token}"},
            json=payload,
            timeout=float(settings.whatsapp_sidecar_timeout_seconds),
        )
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        data = response.json()
    except httpx.TimeoutException:
        return _failed(WHATSAPP_NATIVE_TIMEOUT, "Native WhatsApp sidecar timed out", context, retryable=True)
    except httpx.HTTPStatusError as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        return _failed(WHATSAPP_NATIVE_TRANSPORT_ERROR, f"Native WhatsApp sidecar HTTP {status_code}", context, retryable=status_code is None or int(status_code) >= 500)
    except httpx.HTTPError as exc:
        return _failed(WHATSAPP_NATIVE_TRANSPORT_ERROR, str(exc), context, retryable=True)
    except Exception as exc:
        return _failed(WHATSAPP_NATIVE_BAD_RESPONSE, str(exc), context, retryable=True)
    finally:
        if close_client and hasattr(active_client, "close"):
            active_client.close()

    if not isinstance(data, dict):
        return _failed(WHATSAPP_NATIVE_BAD_RESPONSE, "Native WhatsApp sidecar returned a non-object payload", context, retryable=True)
    if data.get("ok") is True and str(data.get("status") or "") == "sent":
        provider_message_id = _clean(data.get("provider_message_id"))
        if provider_message_id:
            context["provider_message_id"] = provider_message_id
        context["sidecar_status"] = "sent"
        return MessageStatus.sent, "whatsapp_native_sent", _parse_sent_at(data.get("sent_at")), context

    error_code = _clean(data.get("error_code")) or WHATSAPP_NATIVE_BAD_RESPONSE
    retryable = bool(data.get("retryable", True))
    error_message = _clean(data.get("error_message")) or error_code
    return _failed(error_code, error_message, context, retryable=retryable)


def dispatch_whatsapp_native_outbound(
    db: Session,
    *,
    message: TicketOutboundMessage,
    ticket: Ticket | None,
    idempotency_key: str,
    client: SidecarClient | None = None,
) -> tuple[MessageStatus, str | None, object | None, dict[str, Any]]:
    try:
        route = resolve_whatsapp_native_route(db, message=message, ticket=ticket)
    except WhatsAppNativeOutboundError as exc:
        return _failed(exc.failure_code, exc.message, {
            "adapter": "whatsapp_native_sidecar",
            "channel": SourceChannel.whatsapp.value,
            "idempotency_key": idempotency_key,
        }, retryable=exc.retryable)

    return _sidecar_send(
        route=route,
        body=message.body,
        idempotency_key=idempotency_key,
        metadata={
            "ticket_id": message.ticket_id,
            "outbound_message_id": message.id,
        },
        client=client,
    )
