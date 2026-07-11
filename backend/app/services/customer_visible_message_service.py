from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from sqlalchemy.orm import Session

from ..enums import EventType, MessageStatus, NoteVisibility, SourceChannel
from ..models import Ticket, TicketComment, TicketEvent, TicketOutboundMessage
from ..utils.time import utc_now
from ..webchat_models import WebchatConversation, WebchatMessage
from .ai_reply_contract import AIReplyContract, AI_REPLY_CONTRACT_V3
from .message_dispatch import _enforce_customer_visible_origin, _normalize_customer_visible_origin, queue_outbound_message
from .ticket_event_sanitizer import serialize_ticket_event_payload


@dataclass(frozen=True)
class CustomerVisibleMessageResult:
    outbound_message: TicketOutboundMessage | None
    customer_visible: bool
    provider_status: str
    webchat_message: WebchatMessage | None = None
    ticket_comment: TicketComment | None = None
    ticket_event: TicketEvent | None = None


def create_customer_visible_outbound(
    db: Session,
    *,
    ticket: Ticket,
    channel: SourceChannel,
    body: str,
    origin: str,
    created_by: int | None,
    provider_status: str,
    ai_contract: AIReplyContract | None = None,
    status: MessageStatus | None = None,
    subject: str | None = None,
) -> CustomerVisibleMessageResult:
    if ai_contract and ai_contract.contract_version == AI_REPLY_CONTRACT_V3 and ai_contract.reply_type == "null_reply":
        return CustomerVisibleMessageResult(outbound_message=None, customer_visible=False, provider_status="runtime_null_reply_not_sent")

    runtime_payload_json = None
    runtime_payload_sha256 = None
    runtime_reply_type = None
    if ai_contract is not None:
        runtime_payload_json = ai_contract.payload_json(body=body, origin=origin, customer_visible=True)
        runtime_payload_sha256 = ai_contract.payload_sha256(body=body, origin=origin, customer_visible=True)
        runtime_reply_type = ai_contract.reply_type

    _enforce_customer_visible_origin(
        body=body,
        origin=_normalize_customer_visible_origin(origin, created_by=created_by),
        ticket=ticket,
        created_by=created_by,
        runtime_trace_id=ai_contract.runtime_trace_id if ai_contract else None,
        runtime_contract_version=ai_contract.contract_version if ai_contract else None,
        runtime_signature=ai_contract.runtime_signature if ai_contract else None,
        runtime_contract_payload_json=runtime_payload_json,
        runtime_contract_payload_sha256=runtime_payload_sha256,
        runtime_reply_type=runtime_reply_type,
        safety_status=ai_contract.safety_status if ai_contract else None,
    )

    if status == MessageStatus.sent or channel == SourceChannel.web_chat:
        outbound_message = TicketOutboundMessage(
            ticket_id=ticket.id,
            channel=channel,
            status=MessageStatus.sent,
            subject=subject,
            body=body,
            origin=origin,
            runtime_trace_id=ai_contract.runtime_trace_id if ai_contract else None,
            runtime_contract_version=ai_contract.contract_version if ai_contract else None,
            runtime_signature=ai_contract.runtime_signature if ai_contract else None,
            runtime_contract_payload_json=runtime_payload_json,
            runtime_contract_payload_sha256=runtime_payload_sha256,
            runtime_reply_type=runtime_reply_type,
            safety_status=ai_contract.safety_status if ai_contract else None,
            provider_status=provider_status,
            error_message=None,
            created_by=created_by,
            sent_at=utc_now(),
            max_retries=0,
            failure_code=None,
            failure_reason=None,
        )
        db.add(outbound_message)
        db.flush()
        return CustomerVisibleMessageResult(outbound_message=outbound_message, customer_visible=True, provider_status=provider_status)

    outbound_message = queue_outbound_message(
        db,
        ticket_id=ticket.id,
        channel=channel,
        body=body,
        created_by=created_by,
        subject=subject,
        provider_status=provider_status,
        origin=origin,
        runtime_trace_id=ai_contract.runtime_trace_id if ai_contract else None,
        runtime_contract_version=ai_contract.contract_version if ai_contract else None,
        runtime_signature=ai_contract.runtime_signature if ai_contract else None,
        runtime_contract_payload_json=runtime_payload_json,
        runtime_contract_payload_sha256=runtime_payload_sha256,
        runtime_reply_type=runtime_reply_type,
        safety_status=ai_contract.safety_status if ai_contract else None,
    )
    return CustomerVisibleMessageResult(outbound_message=outbound_message, customer_visible=True, provider_status=provider_status)


def create_customer_visible_message(
    db: Session,
    *,
    ticket: Ticket,
    channel: SourceChannel,
    body: str,
    origin: str,
    created_by: int | None,
    provider_status: str,
    conversation: WebchatConversation | None = None,
    ai_contract: AIReplyContract | None = None,
    outbound_status: MessageStatus | None = None,
    subject: str | None = None,
    webchat_direction: str = "agent",
    message_type: str = "text",
    payload_json: str | None = None,
    metadata_json: dict[str, Any] | str | None = None,
    ai_turn_id: int | None = None,
    delivery_status: str | None = None,
    author_label: str | None = None,
    author_user_id: int | None = None,
    safety_level: str | None = None,
    safety_reasons_json: str | None = None,
    create_external_comment: bool = True,
    comment_author_id: int | None = None,
    event_type: EventType | None = None,
    event_note: str | None = None,
    event_payload: dict[str, Any] | None = None,
) -> CustomerVisibleMessageResult:
    if ai_contract and ai_contract.contract_version == AI_REPLY_CONTRACT_V3 and ai_contract.reply_type == "null_reply":
        return CustomerVisibleMessageResult(outbound_message=None, customer_visible=False, provider_status="runtime_null_reply_not_sent")

    outbound_result = create_customer_visible_outbound(
        db,
        ticket=ticket,
        channel=channel,
        body=body,
        origin=origin,
        created_by=created_by,
        provider_status=provider_status,
        ai_contract=ai_contract,
        status=outbound_status,
        subject=subject,
    )

    resolved_delivery_status = delivery_status or ("queued" if channel == SourceChannel.whatsapp else "sent")
    webchat_message: WebchatMessage | None = None
    if conversation is not None:
        webchat_message = WebchatMessage(
            conversation_id=conversation.id,
            ticket_id=ticket.id,
            direction=webchat_direction,
            body=body,
            body_text=body,
            message_type=message_type,
            payload_json=payload_json,
            metadata_json=_json_or_none(metadata_json),
            ai_turn_id=ai_turn_id,
            delivery_status=resolved_delivery_status,
            author_label=author_label,
            author_user_id=author_user_id,
            safety_level=safety_level,
            safety_reasons_json=safety_reasons_json,
        )
        db.add(webchat_message)
        db.flush()

    ticket_comment: TicketComment | None = None
    if create_external_comment:
        ticket_comment = TicketComment(ticket_id=ticket.id, author_id=comment_author_id, body=body, visibility=NoteVisibility.external)
        db.add(ticket_comment)
        db.flush()

    ticket_event: TicketEvent | None = None
    if event_type is not None:
        payload = dict(event_payload or {})
        payload.setdefault("ticket_id", ticket.id)
        payload.setdefault("provider_status", provider_status)
        payload.setdefault("reply_channel", channel.value if hasattr(channel, "value") else str(channel))
        payload.setdefault("external_send", channel == SourceChannel.whatsapp)
        if conversation is not None:
            payload.setdefault("conversation_public_id", conversation.public_id)
            payload.setdefault("conversation_id", conversation.id)
        if webchat_message is not None:
            payload.setdefault("webchat_message_id", webchat_message.id)
        if outbound_result.outbound_message is not None:
            payload.setdefault("outbound_message_id", outbound_result.outbound_message.id)
        ticket_event = TicketEvent(
            ticket_id=ticket.id,
            actor_id=created_by,
            event_type=event_type,
            note=event_note,
            payload_json=serialize_ticket_event_payload(payload),
        )
        db.add(ticket_event)
        db.flush()

    return CustomerVisibleMessageResult(
        outbound_message=outbound_result.outbound_message,
        webchat_message=webchat_message,
        ticket_comment=ticket_comment,
        ticket_event=ticket_event,
        customer_visible=True,
        provider_status=provider_status,
    )


def record_runtime_null_reply(
    db: Session,
    *,
    ticket: Ticket,
    ai_contract: AIReplyContract,
    provider_status: str = "runtime_null_reply_not_sent",
) -> CustomerVisibleMessageResult:
    # Intentionally no TicketOutboundMessage: null_reply is not customer-visible.
    ticket.last_runtime_reply_at = utc_now()
    db.flush()
    return CustomerVisibleMessageResult(outbound_message=None, customer_visible=False, provider_status=provider_status)


def _json_or_none(value: dict[str, Any] | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)
