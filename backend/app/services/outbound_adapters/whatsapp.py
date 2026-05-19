from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable

from sqlalchemy.orm import Session

from ...enums import MessageStatus, SourceChannel
from ...models import ChannelAccount, OpenClawConversationLink, Ticket, TicketOutboundMessage
from ...utils.time import utc_now
from ..openclaw_bridge import dispatch_via_openclaw_bridge

DispatchFn = Callable[..., tuple[MessageStatus, str | None, object | None]]


@dataclass(frozen=True)
class WhatsAppOutboundRoute:
    channel: str
    target: str
    account_id: str
    thread_id: str | None
    session_key: str | None
    source: str

    def to_context(self, *, idempotency_key: str) -> dict[str, Any]:
        payload = asdict(self)
        payload["idempotency_key"] = idempotency_key
        payload["adapter"] = "whatsapp_openclaw_bridge"
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


def _resolve_whatsapp_account(db: Session, *, ticket: Ticket | None, link: OpenClawConversationLink | None) -> tuple[ChannelAccount | None, str]:
    if link is not None:
        row = _active_account_by_pk(db, getattr(link, "channel_account_id", None))
        if row is not None:
            return row, "openclaw_link.channel_account_id"
        row = _active_account_by_id(db, getattr(link, "account_id", None))
        if row is not None:
            return row, "openclaw_link.account_id"
    if ticket is not None:
        row = _active_account_by_pk(db, getattr(ticket, "channel_account_id", None))
        if row is not None:
            return row, "ticket.channel_account_id"
        row = _active_account_for_market(db, getattr(ticket, "market_id", None))
        if row is not None:
            return row, "market_or_global_whatsapp_account"
    return None, "missing_whatsapp_channel_account"


def resolve_whatsapp_outbound_route(db: Session, *, message: TicketOutboundMessage, ticket: Ticket | None) -> WhatsAppOutboundRoute:
    if message.channel != SourceChannel.whatsapp:
        raise ValueError("whatsapp_adapter_channel_mismatch")

    link = ticket.openclaw_link if ticket is not None else None
    target = None
    session_key = None
    thread_id = None
    source = "ticket"

    if link is not None:
        session_key = _clean(link.session_key)
        target = _clean(link.recipient)
        thread_id = _clean(link.thread_id)
        source = "openclaw_link"

    if target is None and ticket is not None:
        target = _clean(ticket.source_chat_id) or _clean(ticket.preferred_reply_contact)
        if target is None and ticket.customer is not None:
            target = _clean(ticket.customer.phone)

    if target is None:
        raise ValueError("missing_whatsapp_target")

    account, account_source = _resolve_whatsapp_account(db, ticket=ticket, link=link)
    if account is None:
        raise ValueError("missing_whatsapp_channel_account")

    return WhatsAppOutboundRoute(
        channel=SourceChannel.whatsapp.value,
        target=target,
        account_id=account.account_id,
        thread_id=thread_id,
        session_key=session_key,
        source=f"{source}:{account_source}",
    )


def dispatch_whatsapp_outbound(
    db: Session,
    *,
    message: TicketOutboundMessage,
    ticket: Ticket | None,
    idempotency_key: str,
    dispatch_fn: DispatchFn | None = None,
) -> tuple[MessageStatus, str | None, object | None, dict[str, Any]]:
    route = resolve_whatsapp_outbound_route(db, message=message, ticket=ticket)
    active_dispatch = dispatch_fn or dispatch_via_openclaw_bridge
    status_value, provider_status, sent_at = active_dispatch(
        channel=route.channel,
        target=route.target,
        body=message.body,
        account_id=route.account_id,
        thread_id=route.thread_id,
        session_key=route.session_key,
    )
    if status_value == MessageStatus.sent and sent_at is None:
        sent_at = utc_now()
    return status_value, provider_status, sent_at, route.to_context(idempotency_key=idempotency_key)
