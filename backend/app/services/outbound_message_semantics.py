from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..enums import MessageStatus, SourceChannel
from ..models import TicketOutboundMessage

EXTERNAL_OUTBOUND_CHANNELS: tuple[SourceChannel, ...] = (
    SourceChannel.whatsapp,
    SourceChannel.telegram,
    SourceChannel.sms,
    SourceChannel.email,
)

WEBCHAT_LOCAL_ACK_PROVIDER_STATUSES = {
    "webchat_safe_ack_delivered",
    "webchat_delivered",
}

WEBCHAT_AI_SAFE_FALLBACK_PROVIDER_STATUSES = {
    "webchat_ai_safe_fallback",
}

AI_REVIEW_REQUIRED_PROVIDER_STATUSES = {
    "ai_review_required",
    "safety_review_required",
}


def channel_value(channel: SourceChannel | str | None) -> str:
    if channel is None:
        return ""
    return channel.value if hasattr(channel, "value") else str(channel)


def provider_status_value(provider_status: str | None) -> str:
    return (provider_status or "").strip().lower()


def is_external_outbound_channel(channel: SourceChannel | str | None) -> bool:
    return channel_value(channel) in {item.value for item in EXTERNAL_OUTBOUND_CHANNELS}


def is_webchat_local_ack(row: TicketOutboundMessage) -> bool:
    return (
        channel_value(row.channel) == SourceChannel.web_chat.value
        and row.status == MessageStatus.sent
        and provider_status_value(row.provider_status) in WEBCHAT_LOCAL_ACK_PROVIDER_STATUSES
    )


def is_webchat_ai_safe_fallback(row: TicketOutboundMessage) -> bool:
    return (
        channel_value(row.channel) == SourceChannel.web_chat.value
        and row.status == MessageStatus.sent
        and provider_status_value(row.provider_status) in WEBCHAT_AI_SAFE_FALLBACK_PROVIDER_STATUSES
    )


def is_external_send_candidate(row: TicketOutboundMessage) -> bool:
    return is_external_outbound_channel(row.channel)


def outbound_ui_label(*, channel: SourceChannel | str | None, status: MessageStatus | str | None, provider_status: str | None) -> str:
    normalized_provider_status = provider_status_value(provider_status)
    normalized_channel = channel_value(channel)
    normalized_status = status.value if hasattr(status, "value") else str(status or "")

    if normalized_channel == SourceChannel.web_chat.value and normalized_provider_status in WEBCHAT_LOCAL_ACK_PROVIDER_STATUSES:
        return "Local WebChat ACK"
    if normalized_channel == SourceChannel.web_chat.value and normalized_provider_status in WEBCHAT_AI_SAFE_FALLBACK_PROVIDER_STATUSES:
        return "WebChat Safe Fallback"
    if normalized_provider_status in AI_REVIEW_REQUIRED_PROVIDER_STATUSES or normalized_status == MessageStatus.draft.value:
        return "Draft / Review Required"
    if is_external_outbound_channel(normalized_channel) and normalized_status in {MessageStatus.pending.value, MessageStatus.processing.value}:
        return "External Send Pending"
    if is_external_outbound_channel(normalized_channel) and normalized_status == MessageStatus.dead.value:
        return "External Send Dead"
    if is_external_outbound_channel(normalized_channel) and normalized_status == MessageStatus.sent.value:
        return "External Send Sent"
    return provider_status or normalized_status or "Unknown"


def external_outbound_query(db: Session):
    return db.query(TicketOutboundMessage).filter(TicketOutboundMessage.channel.in_(list(EXTERNAL_OUTBOUND_CHANNELS)))


def count_outbound_semantics(db: Session) -> dict[str, int]:
    external_base = external_outbound_query(db)
    return {
        "external_pending_outbound": external_base.filter(TicketOutboundMessage.status == MessageStatus.pending).count(),
        "external_dead_outbound": external_base.filter(TicketOutboundMessage.status == MessageStatus.dead).count(),
        "webchat_local_ack_sent": db.query(TicketOutboundMessage)
        .filter(
            TicketOutboundMessage.channel == SourceChannel.web_chat,
            TicketOutboundMessage.status == MessageStatus.sent,
            TicketOutboundMessage.provider_status.in_(WEBCHAT_LOCAL_ACK_PROVIDER_STATUSES),
        )
        .count(),
        "webchat_ai_safe_fallback_sent": db.query(TicketOutboundMessage)
        .filter(
            TicketOutboundMessage.channel == SourceChannel.web_chat,
            TicketOutboundMessage.status == MessageStatus.sent,
            TicketOutboundMessage.provider_status.in_(WEBCHAT_AI_SAFE_FALLBACK_PROVIDER_STATUSES),
        )
        .count(),
    }


def serialize_outbound_semantics(row: TicketOutboundMessage) -> dict[str, Any]:
    return {
        "id": row.id,
        "channel": channel_value(row.channel),
        "status": row.status.value if hasattr(row.status, "value") else str(row.status),
        "provider_status": row.provider_status,
        "ui_label": outbound_ui_label(channel=row.channel, status=row.status, provider_status=row.provider_status),
        "is_external_send": is_external_send_candidate(row),
        "counts_as_external_pending": is_external_send_candidate(row) and row.status == MessageStatus.pending,
    }
