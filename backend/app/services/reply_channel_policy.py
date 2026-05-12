from __future__ import annotations

from dataclasses import dataclass

from ..enums import SourceChannel
from ..models import Ticket
from .outbound_semantics import validate_customer_outbound_channel


class ReplyTargetError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ReplyTarget:
    channel: SourceChannel
    contact: str


def _clean(value: str | None) -> str | None:
    cleaned = (value or '').strip()
    return cleaned or None


def resolve_ticket_reply_target(ticket: Ticket) -> ReplyTarget:
    """Resolve the only valid customer reply target for a ticket.

    Policy for the P0 closure:
    - WebChat source tickets can only become Email replies.
    - WhatsApp source tickets can only become WhatsApp replies.
    - Manual/API tickets may use explicit email/whatsapp if a contact exists.
    - There is no implicit fallback to WhatsApp.
    """
    source_channel = ticket.source_channel

    if source_channel == SourceChannel.web_chat:
        contact = _clean(ticket.preferred_reply_contact)
        if not contact:
            raise ReplyTargetError('customer_email_required_for_webchat_intake')
        return ReplyTarget(channel=SourceChannel.email, contact=contact)

    if source_channel == SourceChannel.whatsapp:
        if ticket.preferred_reply_channel != SourceChannel.whatsapp.value:
            raise ReplyTargetError('whatsapp_reply_channel_required')
        contact = _clean(ticket.preferred_reply_contact) or _clean(ticket.source_chat_id)
        if not contact:
            raise ReplyTargetError('whatsapp_reply_target_required')
        return ReplyTarget(channel=SourceChannel.whatsapp, contact=contact)

    if ticket.preferred_reply_channel:
        try:
            channel = SourceChannel(ticket.preferred_reply_channel)
        except Exception as exc:
            raise ReplyTargetError('preferred_reply_channel_invalid') from exc
        validate_customer_outbound_channel(channel)
        contact = _clean(ticket.preferred_reply_contact)
        if not contact:
            raise ReplyTargetError('preferred_reply_contact_required')
        return ReplyTarget(channel=channel, contact=contact)

    raise ReplyTargetError('no_valid_customer_reply_channel')
