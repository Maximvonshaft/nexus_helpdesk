from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..enums import MessageStatus, SourceChannel
from ..models import Ticket, TicketOutboundMessage
from ..utils.time import utc_now
from .ai_reply_contract import AIReplyContract, AI_REPLY_CONTRACT_V3
from .message_dispatch import _enforce_customer_visible_origin, _normalize_customer_visible_origin, queue_outbound_message


@dataclass(frozen=True)
class CustomerVisibleMessageResult:
    outbound_message: TicketOutboundMessage | None
    customer_visible: bool
    provider_status: str


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

    if origin != "handoff_notice":
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
