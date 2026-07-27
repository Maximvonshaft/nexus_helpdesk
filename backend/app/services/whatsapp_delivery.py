from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ..enums import MessageStatus, SourceChannel
from ..models import TicketOutboundMessage
from ..models_whatsapp import WhatsAppConnection
from ..models_whatsapp_outbound import WhatsAppOutboundPart
from ..utils.time import utc_now


_DELIVERY_RANK = {
    "queued": 0,
    "accepted": 10,
    "sent": 20,
    "delivered": 30,
    "read": 40,
}
_FAILURE_STATES = {"failed", "expired", "revoked"}
_SUCCESS_STATES = set(_DELIVERY_RANK)


@dataclass(frozen=True)
class WhatsAppDeliveryResult:
    updated: bool
    reason: str
    outbound_message_id: int | None
    delivery_status: str | None
    outbound_part_id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "updated": self.updated,
            "reason": self.reason,
            "outbound_message_id": self.outbound_message_id,
            "outbound_part_id": self.outbound_part_id,
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
    outbound_part_id: int | None = None,
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
            outbound_part_id=None,
            delivery_status=normalized_status or None,
        )
    part = _resolve_outbound_part(
        db,
        connection=connection,
        provider_message_id=provider_message_id,
        outbound_message_id=outbound_message_id,
        outbound_part_id=outbound_part_id,
    )
    if part is not None:
        return _apply_part_delivery(
            db,
            connection=connection,
            part=part,
            expected_outbound_message_id=outbound_message_id,
            provider_message_id=provider_message_id,
            normalized_status=normalized_status,
            occurred_at=occurred_at,
            provider=provider,
            receipt_id=receipt_id,
            error_code=error_code,
            error_message=error_message,
            detail=detail,
            payload=payload,
        )
    if outbound_part_id is not None:
        return WhatsAppDeliveryResult(
            updated=False,
            reason="outbound_part_not_found",
            outbound_message_id=outbound_message_id,
            outbound_part_id=outbound_part_id,
            delivery_status=normalized_status,
        )
    return _apply_legacy_parent_delivery(
        db,
        connection=connection,
        provider_message_id=provider_message_id,
        normalized_status=normalized_status,
        occurred_at=occurred_at,
        provider=provider,
        receipt_id=receipt_id,
        outbound_message_id=outbound_message_id,
        error_code=error_code,
        error_message=error_message,
        detail=detail,
        payload=payload,
    )


def _resolve_outbound_part(
    db: Session,
    *,
    connection: WhatsAppConnection,
    provider_message_id: str | None,
    outbound_message_id: int | None,
    outbound_part_id: int | None,
) -> WhatsAppOutboundPart | None:
    base = db.query(WhatsAppOutboundPart).filter(
        WhatsAppOutboundPart.connection_id == connection.id,
        WhatsAppOutboundPart.tenant_id == connection.tenant_id,
    )
    if outbound_part_id is not None:
        return base.filter(WhatsAppOutboundPart.id == int(outbound_part_id)).first()
    if provider_message_id:
        row = base.filter(
            WhatsAppOutboundPart.provider_message_id == provider_message_id,
        ).first()
        if row is not None:
            return row
    if outbound_message_id is None:
        return None
    query = (
        base.filter(
            WhatsAppOutboundPart.outbound_message_id == int(outbound_message_id),
            WhatsAppOutboundPart.provider_message_id.is_(None),
            WhatsAppOutboundPart.status.in_(("queued", "failed")),
        )
        .order_by(WhatsAppOutboundPart.sequence.asc())
    )
    if db.get_bind().dialect.name.startswith("postgresql"):
        query = query.with_for_update(skip_locked=True)
    return query.first()


def _apply_part_delivery(
    db: Session,
    *,
    connection: WhatsAppConnection,
    part: WhatsAppOutboundPart,
    expected_outbound_message_id: int | None,
    provider_message_id: str | None,
    normalized_status: str,
    occurred_at: datetime | None,
    provider: str,
    receipt_id: str | None,
    error_code: str | None,
    error_message: str | None,
    detail: str | None,
    payload: dict[str, Any] | None,
) -> WhatsAppDeliveryResult:
    parent = part.outbound_message
    ticket = parent.ticket if parent is not None else None
    if (
        parent is None
        or ticket is None
        or part.connection_id != connection.id
        or part.tenant_id != connection.tenant_id
        or ticket.channel_account_id != connection.channel_account_id
        or ticket.tenant_id != connection.tenant_id
        or (
            expected_outbound_message_id is not None
            and parent.id != int(expected_outbound_message_id)
        )
    ):
        return WhatsAppDeliveryResult(
            updated=False,
            reason="delivery_scope_mismatch",
            outbound_message_id=part.outbound_message_id,
            outbound_part_id=part.id,
            delivery_status=part.status,
        )
    current = str(part.status or "queued").strip().lower()
    stale_reason = _stale_reason(current, normalized_status)
    if stale_reason:
        return WhatsAppDeliveryResult(
            updated=False,
            reason=stale_reason,
            outbound_message_id=parent.id,
            outbound_part_id=part.id,
            delivery_status=current,
        )
    if provider_message_id:
        if part.provider_message_id and part.provider_message_id != provider_message_id:
            return WhatsAppDeliveryResult(
                updated=False,
                reason="provider_message_id_mismatch",
                outbound_message_id=parent.id,
                outbound_part_id=part.id,
                delivery_status=current,
            )
        part.provider_message_id = provider_message_id
    event_at = occurred_at or utc_now()
    part.status = normalized_status
    part.receipt_at = event_at
    if normalized_status == "sent" and part.sent_at is None:
        part.sent_at = event_at
    elif normalized_status == "delivered":
        part.sent_at = part.sent_at or event_at
        part.delivered_at = event_at
    elif normalized_status == "read":
        part.sent_at = part.sent_at or event_at
        part.delivered_at = part.delivered_at or event_at
        part.read_at = event_at
    if normalized_status in _FAILURE_STATES:
        part.failure_code = (error_code or normalized_status)[:120]
        part.failure_reason = (
            error_message or detail or normalized_status
        )[:1000]
    else:
        part.failure_code = None
        part.failure_reason = None
    part.updated_at = utc_now()
    db.flush()
    aggregate = _aggregate_parent(db, parent=parent, provider=provider)
    parent.delivery_receipt_provider = f"whatsapp_{provider}"
    parent.delivery_receipt_id = receipt_id or provider_message_id
    parent.delivery_receipt_at = event_at
    parent.delivery_detail = detail[:1000] if detail else None
    parent.delivery_payload_json = json.dumps(
        payload or {},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )[:12000]
    parent.last_attempt_at = event_at
    parent.updated_at = utc_now()
    connection.last_outbound_at = event_at
    connection.updated_at = utc_now()
    db.flush()
    return WhatsAppDeliveryResult(
        updated=True,
        reason="delivery_updated",
        outbound_message_id=parent.id,
        outbound_part_id=part.id,
        delivery_status=aggregate,
    )


def _aggregate_parent(
    db: Session,
    *,
    parent: TicketOutboundMessage,
    provider: str,
) -> str:
    parts = (
        db.query(WhatsAppOutboundPart)
        .filter(WhatsAppOutboundPart.outbound_message_id == parent.id)
        .order_by(WhatsAppOutboundPart.sequence.asc())
        .all()
    )
    statuses = [str(part.status or "queued").lower() for part in parts]
    if not statuses:
        return str(parent.delivery_status or "queued")
    failure = next(
        (status for status in statuses if status in _FAILURE_STATES),
        None,
    )
    if failure:
        aggregate = failure
        parent.status = MessageStatus.failed
        failed_part = next(part for part in parts if part.status == failure)
        parent.failure_code = failed_part.failure_code or failure
        parent.failure_reason = failed_part.failure_reason or failure
        parent.error_message = parent.failure_reason
    else:
        aggregate = min(
            statuses,
            key=lambda value: _DELIVERY_RANK.get(value, 0),
        )
        if _DELIVERY_RANK.get(aggregate, 0) >= _DELIVERY_RANK["sent"]:
            parent.status = MessageStatus.sent
            if parent.sent_at is None:
                sent_values = [part.sent_at for part in parts if part.sent_at]
                parent.sent_at = min(sent_values) if sent_values else utc_now()
        elif parent.status not in {MessageStatus.pending, MessageStatus.processing}:
            parent.status = MessageStatus.processing
        parent.failure_code = None
        parent.failure_reason = None
        parent.error_message = None
    parent.delivery_status = aggregate
    parent.delivery_event_type = aggregate
    parent.provider_status = f"whatsapp_{provider}_{aggregate}"
    first_provider_id = next(
        (part.provider_message_id for part in parts if part.provider_message_id),
        None,
    )
    if first_provider_id:
        parent.provider_message_id = first_provider_id
    return aggregate


def _stale_reason(current: str, next_status: str) -> str | None:
    if next_status in _DELIVERY_RANK:
        if current in _FAILURE_STATES:
            return "stale_delivery_event"
        if _DELIVERY_RANK[next_status] < _DELIVERY_RANK.get(current, -1):
            return "stale_delivery_event"
        return None
    if _DELIVERY_RANK.get(current, -1) >= _DELIVERY_RANK["delivered"]:
        return "stale_failure_after_delivery"
    return None


def _apply_legacy_parent_delivery(
    db: Session,
    *,
    connection: WhatsAppConnection,
    provider_message_id: str | None,
    normalized_status: str,
    occurred_at: datetime | None,
    provider: str,
    receipt_id: str | None,
    outbound_message_id: int | None,
    error_code: str | None,
    error_message: str | None,
    detail: str | None,
    payload: dict[str, Any] | None,
) -> WhatsAppDeliveryResult:
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
            outbound_part_id=None,
            delivery_status=normalized_status,
        )
    row = query.first()
    if row is None:
        return WhatsAppDeliveryResult(
            updated=False,
            reason="outbound_message_not_found",
            outbound_message_id=None,
            outbound_part_id=None,
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
            outbound_part_id=None,
            delivery_status=row.delivery_status,
        )
    current = str(row.delivery_status or "queued").strip().lower()
    stale_reason = _stale_reason(current, normalized_status)
    if stale_reason:
        return WhatsAppDeliveryResult(
            updated=False,
            reason=stale_reason,
            outbound_message_id=row.id,
            outbound_part_id=None,
            delivery_status=current,
        )
    event_at = occurred_at or utc_now()
    if normalized_status in _SUCCESS_STATES:
        row.status = MessageStatus.sent
        row.failure_code = None
        row.failure_reason = None
        row.error_message = None
        if normalized_status == "sent" and row.sent_at is None:
            row.sent_at = event_at
    else:
        row.status = MessageStatus.failed
        row.failure_code = (error_code or normalized_status)[:120]
        row.failure_reason = (error_message or detail or normalized_status)[:1000]
        row.error_message = row.failure_reason
    if provider_message_id:
        if row.provider_message_id and row.provider_message_id != provider_message_id:
            return WhatsAppDeliveryResult(
                updated=False,
                reason="provider_message_id_mismatch",
                outbound_message_id=row.id,
                outbound_part_id=None,
                delivery_status=current,
            )
        row.provider_message_id = provider_message_id
    row.delivery_status = normalized_status
    row.delivery_event_type = normalized_status
    row.provider_status = f"whatsapp_{provider}_{normalized_status}"
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
        outbound_part_id=None,
        delivery_status=normalized_status,
    )
