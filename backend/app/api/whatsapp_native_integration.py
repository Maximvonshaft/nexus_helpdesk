from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..enums import MessageStatus
from ..models import ChannelAccount, TicketOutboundMessage
from ..services.whatsapp_native_inbound import (
    WhatsAppNativeAuthError,
    WhatsAppNativeInboundError,
    ingest_whatsapp_native_inbound,
    verify_whatsapp_connector_headers,
)
from ..unit_of_work import managed_session
from ..utils.time import utc_now

router = APIRouter(prefix="/api/integrations/whatsapp/native", tags=["whatsapp-native-integration"])


def _health_from_status(value: str | None) -> str:
    status_value = (value or "").strip().lower()
    if status_value == "connected":
        return "healthy"
    if status_value in {"connecting", "qr_pending", "reconnecting", "idle"}:
        return "degraded"
    if status_value in {"disconnected", "error"}:
        return "offline"
    return "unknown"


async def _verified_payload(
    request: Request,
    *,
    connector_key: str | None,
    account_id: str | None,
    timestamp: str | None,
    signature: str | None,
) -> dict[str, Any]:
    raw_body = await request.body()
    try:
        verify_whatsapp_connector_headers(
            raw_body=raw_body,
            connector_key=connector_key,
            account_id=account_id,
            timestamp=timestamp,
            signature=signature,
        )
    except WhatsAppNativeAuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_json") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_payload")
    payload_account_id = str(payload.get("account_id") or account_id or "").strip()
    if not payload_account_id or payload_account_id != str(account_id or "").strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="account_id_mismatch")
    return payload


def _account(db: Session, account_id: str | None) -> ChannelAccount:
    row = (
        db.query(ChannelAccount)
        .filter(ChannelAccount.account_id == account_id, ChannelAccount.provider == "whatsapp")
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="whatsapp_channel_account_not_found")
    return row


def _clip(value: Any, max_length: int) -> str | None:
    text = str(value or "").strip()
    return text[:max_length] if text else None


def _parse_event_at(value: Any):
    text = str(value or "").strip()
    if not text:
        return utc_now()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return utc_now()


def apply_whatsapp_native_delivery_payload(row: TicketOutboundMessage, payload: dict[str, Any]) -> None:
    provider_message_id = _clip(payload.get("provider_message_id"), 255)
    event_at = _parse_event_at(payload.get("sent_at") or payload.get("occurred_at"))
    if str(payload.get("status") or "").lower() == "sent" or payload.get("ok") is True:
        row.status = MessageStatus.sent
        row.provider_status = "whatsapp_native_sent"
        if provider_message_id:
            row.provider_message_id = provider_message_id
        row.sent_at = event_at
        row.delivery_status = "sent"
        row.delivery_receipt_provider = "whatsapp_native"
        row.delivery_receipt_id = provider_message_id or _clip(payload.get("idempotency_key"), 255)
        row.delivery_receipt_at = event_at
        row.error_message = None
        row.failure_code = None
        row.failure_reason = None
    else:
        row.status = MessageStatus.failed
        row.provider_status = "whatsapp_native_failed"
        row.error_message = str(payload.get("error_message") or payload.get("error_code") or "whatsapp_native_delivery_failed")[:500]
        row.failure_code = str(payload.get("error_code") or "whatsapp_native_delivery_failed")[:120]
        row.failure_reason = row.error_message
        row.delivery_status = "failed"
        row.delivery_receipt_provider = "whatsapp_native"
        row.delivery_receipt_id = provider_message_id or _clip(payload.get("idempotency_key"), 255)
        row.delivery_receipt_at = event_at
    row.last_attempt_at = event_at


@router.post("/inbound")
async def whatsapp_native_inbound(
    request: Request,
    x_nexus_connector_key: str | None = Header(default=None, alias="X-Nexus-Connector-Key"),
    x_nexus_account_id: str | None = Header(default=None, alias="X-Nexus-Account-Id"),
    x_nexus_timestamp: str | None = Header(default=None, alias="X-Nexus-Timestamp"),
    x_nexus_signature: str | None = Header(default=None, alias="X-Nexus-Signature"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    payload = await _verified_payload(
        request,
        connector_key=x_nexus_connector_key,
        account_id=x_nexus_account_id,
        timestamp=x_nexus_timestamp,
        signature=x_nexus_signature,
    )
    try:
        with managed_session(db):
            result = ingest_whatsapp_native_inbound(db, payload)
    except WhatsAppNativeInboundError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return result.as_dict()


@router.post("/status")
async def whatsapp_native_status(
    request: Request,
    x_nexus_connector_key: str | None = Header(default=None, alias="X-Nexus-Connector-Key"),
    x_nexus_account_id: str | None = Header(default=None, alias="X-Nexus-Account-Id"),
    x_nexus_timestamp: str | None = Header(default=None, alias="X-Nexus-Timestamp"),
    x_nexus_signature: str | None = Header(default=None, alias="X-Nexus-Signature"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    payload = await _verified_payload(
        request,
        connector_key=x_nexus_connector_key,
        account_id=x_nexus_account_id,
        timestamp=x_nexus_timestamp,
        signature=x_nexus_signature,
    )
    account = _account(db, x_nexus_account_id)
    with managed_session(db):
        account.health_status = _health_from_status(str(payload.get("status") or ""))
        account.last_health_check_at = utc_now()
        db.flush()
    return {"ok": True, "account_id": account.account_id, "health_status": account.health_status}


@router.post("/delivery")
async def whatsapp_native_delivery(
    request: Request,
    x_nexus_connector_key: str | None = Header(default=None, alias="X-Nexus-Connector-Key"),
    x_nexus_account_id: str | None = Header(default=None, alias="X-Nexus-Account-Id"),
    x_nexus_timestamp: str | None = Header(default=None, alias="X-Nexus-Timestamp"),
    x_nexus_signature: str | None = Header(default=None, alias="X-Nexus-Signature"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    payload = await _verified_payload(
        request,
        connector_key=x_nexus_connector_key,
        account_id=x_nexus_account_id,
        timestamp=x_nexus_timestamp,
        signature=x_nexus_signature,
    )
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    outbound_id = metadata.get("outbound_message_id") or payload.get("outbound_message_id")
    if not outbound_id:
        return {"ok": True, "updated": False, "reason": "missing_outbound_message_id"}
    row = db.query(TicketOutboundMessage).filter(TicketOutboundMessage.id == int(outbound_id)).first()
    if row is None:
        return {"ok": True, "updated": False, "reason": "outbound_message_not_found"}
    with managed_session(db):
        apply_whatsapp_native_delivery_payload(row, payload)
        db.flush()
    return {"ok": True, "updated": True, "outbound_message_id": row.id, "status": row.status.value if hasattr(row.status, "value") else str(row.status)}
