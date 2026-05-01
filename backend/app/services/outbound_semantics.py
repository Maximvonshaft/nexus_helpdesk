from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..enums import MessageStatus, SourceChannel
from ..models import TicketOutboundMessage


EXTERNAL_OUTBOUND_CHANNELS = frozenset({
    SourceChannel.whatsapp.value,
    SourceChannel.telegram.value,
    SourceChannel.sms.value,
    SourceChannel.email.value,
})

WEBCHAT_LOCAL_ACK_PROVIDER_STATUSES = frozenset({
    'webchat_delivered',
    'webchat_safe_ack_delivered',
})

WEBCHAT_CARD_PROVIDER_STATUSES = frozenset({
    'webchat_card_delivered',
})

WEBCHAT_HANDOFF_PROVIDER_STATUSES = frozenset({
    'webchat_handoff_ack_delivered',
})

WEBCHAT_AI_DELIVERED_PROVIDER_STATUSES = frozenset({
    'webchat_ai_delivered',
})

WEBCHAT_AI_SAFE_FALLBACK_PROVIDER_STATUSES = frozenset({
    'webchat_ai_safe_fallback',
})

WEBCHAT_LOCAL_ONLY_PROVIDER_STATUSES = frozenset().union(
    WEBCHAT_LOCAL_ACK_PROVIDER_STATUSES,
    WEBCHAT_CARD_PROVIDER_STATUSES,
    WEBCHAT_HANDOFF_PROVIDER_STATUSES,
    WEBCHAT_AI_DELIVERED_PROVIDER_STATUSES,
    WEBCHAT_AI_SAFE_FALLBACK_PROVIDER_STATUSES,
)

DRAFT_REVIEW_PROVIDER_STATUSES = frozenset({
    'ai_review_required',
    'safety_review_required',
})


def _value(raw: Any) -> str:
    if raw is None:
        return ''
    value = getattr(raw, 'value', raw)
    return str(value or '').strip()


def external_channel_values() -> list[str]:
    return sorted(EXTERNAL_OUTBOUND_CHANNELS)


def is_external_outbound_channel(channel: Any) -> bool:
    return _value(channel) in EXTERNAL_OUTBOUND_CHANNELS


def is_external_outbound_message(message: TicketOutboundMessage) -> bool:
    return is_external_outbound_channel(message.channel)


def is_webchat_local_ack(message: TicketOutboundMessage) -> bool:
    return (
        _value(message.channel) == SourceChannel.web_chat.value
        and _value(message.status) == MessageStatus.sent.value
        and _value(message.provider_status) in WEBCHAT_LOCAL_ACK_PROVIDER_STATUSES
    )


def is_webchat_card_delivery(message: TicketOutboundMessage) -> bool:
    return (
        _value(message.channel) == SourceChannel.web_chat.value
        and _value(message.status) == MessageStatus.sent.value
        and _value(message.provider_status) in WEBCHAT_CARD_PROVIDER_STATUSES
    )


def is_webchat_handoff_ack(message: TicketOutboundMessage) -> bool:
    return (
        _value(message.channel) == SourceChannel.web_chat.value
        and _value(message.status) == MessageStatus.sent.value
        and _value(message.provider_status) in WEBCHAT_HANDOFF_PROVIDER_STATUSES
    )


def is_webchat_ai_delivered(message: TicketOutboundMessage) -> bool:
    return (
        _value(message.channel) == SourceChannel.web_chat.value
        and _value(message.status) == MessageStatus.sent.value
        and _value(message.provider_status) in WEBCHAT_AI_DELIVERED_PROVIDER_STATUSES
    )


def is_webchat_ai_safe_fallback(message: TicketOutboundMessage) -> bool:
    return (
        _value(message.channel) == SourceChannel.web_chat.value
        and _value(message.status) == MessageStatus.sent.value
        and _value(message.provider_status) in WEBCHAT_AI_SAFE_FALLBACK_PROVIDER_STATUSES
    )


def is_webchat_local_only_message(message: TicketOutboundMessage) -> bool:
    return (
        _value(message.channel) == SourceChannel.web_chat.value
        and _value(message.provider_status) in WEBCHAT_LOCAL_ONLY_PROVIDER_STATUSES
    )


def is_draft_review_required(message: TicketOutboundMessage) -> bool:
    return _value(message.status) == MessageStatus.draft.value or _value(message.provider_status) in DRAFT_REVIEW_PROVIDER_STATUSES


def is_external_pending_message(message: TicketOutboundMessage) -> bool:
    return is_external_outbound_message(message) and _value(message.status) == MessageStatus.pending.value


def is_external_dead_message(message: TicketOutboundMessage) -> bool:
    return is_external_outbound_message(message) and _value(message.status) == MessageStatus.dead.value


def outbound_ui_label(channel: Any, status: Any, provider_status: str | None = None) -> str:
    channel_value = _value(channel)
    status_value = _value(status)
    provider_value = _value(provider_status)

    if channel_value == SourceChannel.web_chat.value and provider_value in WEBCHAT_LOCAL_ACK_PROVIDER_STATUSES:
        return 'Local WebChat ACK'
    if channel_value == SourceChannel.web_chat.value and provider_value in WEBCHAT_CARD_PROVIDER_STATUSES:
        return 'Local WebChat Card'
    if channel_value == SourceChannel.web_chat.value and provider_value in WEBCHAT_HANDOFF_PROVIDER_STATUSES:
        return 'Local WebChat Handoff ACK'
    if channel_value == SourceChannel.web_chat.value and provider_value in WEBCHAT_AI_DELIVERED_PROVIDER_STATUSES:
        return 'Local WebChat AI Reply'
    if channel_value == SourceChannel.web_chat.value and provider_value in WEBCHAT_AI_SAFE_FALLBACK_PROVIDER_STATUSES:
        return 'WebChat Safe Fallback'
    if provider_value in DRAFT_REVIEW_PROVIDER_STATUSES or status_value == MessageStatus.draft.value:
        return 'Draft / Review Required'
    if channel_value in EXTERNAL_OUTBOUND_CHANNELS and status_value == MessageStatus.pending.value:
        return 'External Send Pending'
    if channel_value in EXTERNAL_OUTBOUND_CHANNELS and status_value == MessageStatus.sent.value:
        return 'External Send Sent'
    if channel_value in EXTERNAL_OUTBOUND_CHANNELS and status_value == MessageStatus.dead.value:
        return 'External Send Failed'
    if channel_value == SourceChannel.web_chat.value:
        return 'Local WebChat Message'
    return 'Outbound Message'


def outbound_is_external_send(channel: Any, provider_status: str | None = None) -> bool:
    return is_external_outbound_channel(channel)


def count_outbound_semantics(db: Session) -> dict[str, int]:
    external_channels = external_channel_values()
    return {
        'external_pending_outbound': db.query(TicketOutboundMessage).filter(
            TicketOutboundMessage.channel.in_(external_channels),
            TicketOutboundMessage.status == MessageStatus.pending,
        ).count(),
        'external_dead_outbound': db.query(TicketOutboundMessage).filter(
            TicketOutboundMessage.channel.in_(external_channels),
            TicketOutboundMessage.status == MessageStatus.dead,
        ).count(),
        'webchat_local_ack_sent': db.query(TicketOutboundMessage).filter(
            TicketOutboundMessage.channel == SourceChannel.web_chat,
            TicketOutboundMessage.status == MessageStatus.sent,
            TicketOutboundMessage.provider_status.in_(sorted(WEBCHAT_LOCAL_ACK_PROVIDER_STATUSES)),
        ).count(),
        'webchat_card_sent': db.query(TicketOutboundMessage).filter(
            TicketOutboundMessage.channel == SourceChannel.web_chat,
            TicketOutboundMessage.status == MessageStatus.sent,
            TicketOutboundMessage.provider_status.in_(sorted(WEBCHAT_CARD_PROVIDER_STATUSES)),
        ).count(),
        'webchat_handoff_ack_sent': db.query(TicketOutboundMessage).filter(
            TicketOutboundMessage.channel == SourceChannel.web_chat,
            TicketOutboundMessage.status == MessageStatus.sent,
            TicketOutboundMessage.provider_status.in_(sorted(WEBCHAT_HANDOFF_PROVIDER_STATUSES)),
        ).count(),
        'webchat_ai_delivered_sent': db.query(TicketOutboundMessage).filter(
            TicketOutboundMessage.channel == SourceChannel.web_chat,
            TicketOutboundMessage.status == MessageStatus.sent,
            TicketOutboundMessage.provider_status.in_(sorted(WEBCHAT_AI_DELIVERED_PROVIDER_STATUSES)),
        ).count(),
        'webchat_ai_safe_fallback_sent': db.query(TicketOutboundMessage).filter(
            TicketOutboundMessage.channel == SourceChannel.web_chat,
            TicketOutboundMessage.status == MessageStatus.sent,
            TicketOutboundMessage.provider_status.in_(sorted(WEBCHAT_AI_SAFE_FALLBACK_PROVIDER_STATUSES)),
        ).count(),
    }
