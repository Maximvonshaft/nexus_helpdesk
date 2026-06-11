from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from ..enums import MessageStatus, SourceChannel
from ..models import Ticket, TicketOutboundMessage
from ..settings import get_settings
from ..utils.time import utc_now
from ..webchat_models import WebchatConversation


def _value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value or "")


def reply_channel_for_conversation(ticket: Ticket, conversation: WebchatConversation | None = None) -> SourceChannel:
    """Resolve the customer-visible reply channel for a unified WebChat inbox row."""

    candidates = [
        getattr(ticket, "preferred_reply_channel", None),
        _value(getattr(ticket, "source_channel", None)),
        getattr(conversation, "channel_key", None) if conversation is not None else None,
    ]
    for candidate in candidates:
        cleaned = str(candidate or "").strip().lower()
        if not cleaned or cleaned == "default":
            continue
        try:
            return SourceChannel(cleaned)
        except Exception:
            continue
    return SourceChannel.web_chat


def is_external_reply_channel(channel: SourceChannel | str) -> bool:
    return _value(channel) != SourceChannel.web_chat.value


def create_customer_reply_outbound(
    db: Session,
    *,
    ticket: Ticket,
    conversation: WebchatConversation,
    body: str,
    created_by: int | None,
    local_provider_status: str,
    external_provider_status: str | None = None,
    error_message: str | None = None,
    failure_code: str | None = None,
    failure_reason: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> TicketOutboundMessage:
    channel = reply_channel_for_conversation(ticket, conversation)
    external = is_external_reply_channel(channel)
    provider_status = external_provider_status if external else local_provider_status
    row = TicketOutboundMessage(
        ticket_id=ticket.id,
        channel=channel,
        status=MessageStatus.pending if external else MessageStatus.sent,
        body=body,
        provider_status=provider_status or ("queued" if external else local_provider_status),
        error_message=error_message,
        created_by=created_by,
        sent_at=None if external else utc_now(),
        max_retries=get_settings().outbox_max_retries if external else 0,
        failure_code=failure_code,
        failure_reason=failure_reason,
        delivery_payload_json=json.dumps(metadata or {}, ensure_ascii=False) if metadata else None,
    )
    db.add(row)
    db.flush()
    return row
