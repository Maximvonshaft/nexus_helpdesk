from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..enums import EventType, MessageStatus, SourceChannel
from ..models import Ticket, TicketOutboundMessage, User
from ..schemas import EmailDeliveryReceiptRequest
from ..utils.time import ensure_utc, utc_now
from .audit_service import log_admin_audit, log_event
from .email_mailbox_identity import normalize_mailbox_header_id
from .permissions import ensure_can_manage_runtime, ensure_ticket_visible

FINAL_FAILURE_STATUSES = {"bounced", "failed", "rejected", "complained"}
SUCCESS_STATUSES = {"accepted", "delivered", "opened"}
REDACTED_KEYS = ("secret", "token", "password", "authorization", "api_key", "apikey", "key")


@dataclass(frozen=True)
class EmailDeliveryReceiptResult:
    message: TicketOutboundMessage
    created: bool
    ticket_event_id: int | None = None
    audit_id: int | None = None


def _clip(value: Any, limit: int) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text[:limit]


def _redact_payload(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return "[truncated]"
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for index, (key, raw_value) in enumerate(value.items()):
            if index >= 40:
                output["__truncated__"] = True
                break
            key_text = str(key)[:120]
            if any(marker in key_text.lower() for marker in REDACTED_KEYS):
                output[key_text] = "[redacted]"
            else:
                output[key_text] = _redact_payload(raw_value, depth + 1)
        return output
    if isinstance(value, list):
        return [_redact_payload(item, depth + 1) for item in value[:40]]
    if isinstance(value, str):
        return value[:500]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:500]


def _safe_payload_json(payload: dict[str, Any] | None) -> str | None:
    if not payload:
        return None
    return json.dumps(_redact_payload(payload), ensure_ascii=False, sort_keys=True, default=str)


def _message_status_for_receipt(delivery_status: str) -> MessageStatus:
    if delivery_status in SUCCESS_STATUSES:
        return MessageStatus.sent
    if delivery_status == "deferred":
        return MessageStatus.failed
    return MessageStatus.dead


def _event_type_for_receipt(delivery_status: str) -> EventType:
    if delivery_status in SUCCESS_STATUSES:
        return EventType.outbound_sent
    if delivery_status == "deferred":
        return EventType.outbound_retry_scheduled
    return EventType.outbound_dead


def _existing_receipt(message: TicketOutboundMessage, payload: EmailDeliveryReceiptRequest) -> bool:
    receipt_id = _clip(payload.provider_event_id, 255)
    if not receipt_id:
        return False
    return (
        message.delivery_receipt_id == receipt_id
        and message.delivery_receipt_provider == (_clip(payload.provider, 80) or "manual")
        and message.delivery_status == payload.delivery_status
    )


def record_email_delivery_receipt(
    db: Session,
    *,
    ticket_id: int,
    message_id: int,
    payload: EmailDeliveryReceiptRequest,
    current_user: User,
) -> EmailDeliveryReceiptResult:
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    ensure_ticket_visible(current_user, ticket, db)
    ensure_can_manage_runtime(current_user, db)

    message = (
        db.query(TicketOutboundMessage)
        .filter(TicketOutboundMessage.id == message_id, TicketOutboundMessage.ticket_id == ticket.id)
        .first()
    )
    if message is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Outbound message not found")
    if message.channel != SourceChannel.email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="delivery_receipt_email_only")
    if _existing_receipt(message, payload):
        return EmailDeliveryReceiptResult(message=message, created=False)

    occurred_at = ensure_utc(payload.occurred_at) or utc_now()
    delivery_status = payload.delivery_status
    provider = _clip(payload.provider, 80) or "manual"
    provider_event_type = _clip(payload.provider_event_type, 80) or delivery_status
    provider_event_id = _clip(payload.provider_event_id, 255)
    old_value = {
        "status": message.status.value if hasattr(message.status, "value") else str(message.status),
        "provider_status": message.provider_status,
        "delivery_status": message.delivery_status,
        "delivery_receipt_id": message.delivery_receipt_id,
        "failure_code": message.failure_code,
        "failure_reason": message.failure_reason,
    }

    message.status = _message_status_for_receipt(delivery_status)
    message.provider_status = _clip(payload.provider_status, 120) or f"receipt:{delivery_status}"
    if payload.provider_message_id:
        message.provider_message_id = _clip(payload.provider_message_id, 255)
    if payload.mailbox_message_id:
        message.mailbox_message_id = normalize_mailbox_header_id(payload.mailbox_message_id) or _clip(payload.mailbox_message_id, 255)
    message.delivery_status = delivery_status
    message.delivery_event_type = provider_event_type
    message.delivery_receipt_provider = provider
    message.delivery_receipt_id = provider_event_id
    message.delivery_receipt_at = occurred_at
    message.delivery_detail = _clip(payload.detail or payload.failure_reason, 2000)
    message.delivery_payload_json = _safe_payload_json(payload.raw_payload)
    message.updated_at = utc_now()

    if delivery_status in FINAL_FAILURE_STATUSES or delivery_status == "deferred":
        message.failure_code = _clip(payload.failure_code, 120) or delivery_status
        message.failure_reason = _clip(payload.failure_reason or payload.detail, 2000) or f"Email delivery receipt marked {delivery_status}"
    else:
        message.failure_code = None
        message.failure_reason = None
        if message.sent_at is None:
            message.sent_at = occurred_at

    event_payload = {
        "message_id": message.id,
        "delivery_status": message.delivery_status,
        "delivery_event_type": message.delivery_event_type,
        "delivery_receipt_provider": message.delivery_receipt_provider,
        "delivery_receipt_id": message.delivery_receipt_id,
        "delivery_receipt_at": message.delivery_receipt_at.isoformat() if message.delivery_receipt_at else None,
        "delivery_detail": message.delivery_detail,
        "provider_status": message.provider_status,
        "provider_message_id": message.provider_message_id,
        "mailbox_message_id": message.mailbox_message_id,
        "failure_code": message.failure_code,
        "failure_reason": message.failure_reason,
    }
    event = log_event(
        db,
        ticket_id=ticket.id,
        actor_id=current_user.id,
        event_type=_event_type_for_receipt(delivery_status),
        field_name="email.delivery_receipt",
        note="Email delivery receipt recorded",
        payload=event_payload,
    )
    audit = log_admin_audit(
        db,
        actor_id=current_user.id,
        action="email.delivery_receipt.ingested",
        target_type="ticket_outbound_message",
        target_id=message.id,
        old_value=old_value,
        new_value=event_payload,
    )
    db.flush()
    return EmailDeliveryReceiptResult(message=message, created=True, ticket_event_id=event.id, audit_id=audit.id)
