from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ..enums import MessageStatus, SourceChannel
from ..models import TicketOutboundMessage
from ..models_whatsapp import WhatsAppConnection
from ..utils.time import utc_now


_DELIVERY_RANK = {
    "queued": 0,
    "accepted": 10,
    "sent": 20,
    "delivered": 30,
    "read": 40,
}
_FAILURE_STATES = {"failed", "expired", "revoked"}


@dataclass(frozen=True)
class WhatsAppDeliveryResult:
    updated: bool
    reason: str
    outbound_message_id: int | None
    delivery_status: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "updated": self.updated,
            "reason": self.reason,
            "outbound_message_id": self.outbound_message_id,
            "delivery_status": self.delivery_status,
        }


def apply_whatsapp_delivery(
    db: Session,
    *,
    connection: WhatsAppConnection,
    provider_message_id: str | None,
    status: str,
    occurred_at: datetime | None,
    provider: str,
    receipt_id: str | None = None,
    outbound_message_id: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    detail: str | None = None,
    payload: dict[str, Any] | None = None,
) -> WhatsAppDeliveryResult:
    normalized_status = str(status or "").strip().lower()
    if normalized_status not in {*_DELIVERY_RANK, *_FAILURE_STATES}:
        return WhatsAppDeliveryResult(
            updated=False,
            reason="unsupported_delivery_status",
            outbound_message_id=None,
            delivery_status=normalized_status or None,
        )

    query = db.query(TicketOutboundMessage).filter(
        TicketOutboundMessage.channel == SourceChannel.whatsapp,
    )
    if outbound_message_id is not None:
        query = query.filter(TicketOutboundMessage.id == int(outbound_message_id))
    elif provider_message_id:
        query = query.filter(
            TicketOutboundMessage.provider_message_id == provider_message_id
        )
    else:
        return WhatsAppDeliveryResult(
            updated=False,
            reason="delivery_identity_missing",
            outbound_message_id=None,
            delivery_status=normalized_status,
        )
    row = query.first()
    if row is None:
        return WhatsAppDeliveryResult(
            updated=False,
            reason="outbound_message_not_found",
            outbound_message_id=None,
            delivery_status=normalized_status,
        )
    ticket = row.ticket
    if (
        ticket is None
        or ticket.channel_account_id != connection.channel_account_id
        or ticket.tenant_id != connection.tenant_id
    ):
        return WhatsAppDeliveryResult(
            updated=False,
            reason="delivery_scope_mismatch",
            outbound_message_id=row.id,
            delivery_status=row.delivery_status,
        )

    current = str(row.delivery_status or "queued").strip().lower()
    if normalized_status in _DELIVERY_RANK:
        current_rank = _DELIVERY_RANK.get(current, -1)
        next_rank = _DELIVERY_RANK[normalized_status]
        if current in _FAILURE_STATES or next_rank < current_rank:
            return WhatsAppDeliveryResult(
                updated=False,
                reason="stale_delivery_event",
                outbound_message_id=row.id,
                delivery_status=current,
            )
        row.status = MessageStatus.sent
        row.delivery_status = normalized_status
        row.provider_status = f"whatsapp_{provider}_{normalized_status}"
        row.failure_code = None
        row.failure_reason = None
        row.error_message = None
        if normalized_status == "sent" and row.sent_at is None:
            row.sent_at = occurred_at or utc_now()
    else:
        # A late failure cannot regress a delivery that the provider already
        # confirmed as delivered or read.
        if _DELIVERY_RANK.get(current, -1) >= _DELIVERY_RANK["delivered"]:
            return WhatsAppDeliveryResult(
                updated=False,
                reason="stale_failure_after_delivery",
                outbound_message_id=row.id,
                delivery_status=current,
            )
        row.status = MessageStatus.failed
        row.delivery_status = normalized_status
        row.provider_status = f"whatsapp_{provider}_{normalized_status}"
        row.failure_code = (error_code or normalized_status)[:120]
        row.failure_reason = (error_message or detail or normalized_status)[:1000]
        row.error_message = row.failure_reason

    event_at = occurred_at or utc_now()
    if provider_message_id:
        if row.provider_message_id and row.provider_message_id != provider_message_id:
            return WhatsAppDeliveryResult(
                updated=False,
                reason="provider_message_id_mismatch",
                outbound_message_id=row.id,
                delivery_status=current,
            )
        row.provider_message_id = provider_message_id
    row.delivery_event_type = normalized_status
    row.delivery_receipt_provider = f"whatsapp_{provider}"
    row.delivery_receipt_id = receipt_id or provider_message_id
    row.delivery_receipt_at = event_at
    row.delivery_detail = detail[:1000] if detail else None
    row.delivery_payload_json = json.dumps(
        payload or {},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )[:12000]
    row.last_attempt_at = event_at
    row.updated_at = utc_now()
    connection.last_outbound_at = event_at
    connection.updated_at = utc_now()
    db.flush()
    return WhatsAppDeliveryResult(
        updated=True,
        reason="delivery_updated",
        outbound_message_id=row.id,
        delivery_status=normalized_status,
    )
