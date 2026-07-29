from __future__ import annotations

import hmac
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ChannelAccount
from ..models_whatsapp import WhatsAppConnection
from ..services.secret_crypto import SecretCryptoService
from ..services.whatsapp_connection_service import apply_observed_snapshot
from ..services.whatsapp_delivery import apply_whatsapp_delivery
from ..services.whatsapp_inbound import (
    WhatsAppConnectorAuthError,
    WhatsAppInboundError,
    ingest_whatsapp_inbound,
    verify_whatsapp_connector_headers,
)
from ..services.whatsapp_meta_webhook import (
    iter_meta_delivery_events,
    iter_meta_inbound_messages,
    verify_meta_webhook_signature,
)
from ..unit_of_work import managed_session
from ..utils.time import utc_now


router = APIRouter(
    prefix="/api/integrations/whatsapp",
    tags=["whatsapp-integration"],
)

_IGNORED_NON_CUSTOMER_CHAT = "ignored_whatsapp_non_customer_chat"


def _crypto() -> SecretCryptoService:
    try:
        return SecretCryptoService.whatsapp()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="whatsapp_secret_runtime_unavailable",
        ) from exc


def _json_payload(raw_body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_json",
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_payload",
        )
    return payload


def _baileys_connection(
    db: Session,
    session_key: str | None,
) -> WhatsAppConnection:
    key = str(session_key or "").strip()
    if not key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="missing_account_id",
        )
    row = (
        db.query(WhatsAppConnection)
        .join(
            ChannelAccount,
            ChannelAccount.id == WhatsAppConnection.channel_account_id,
        )
        .filter(
            WhatsAppConnection.transport == "baileys_sidecar",
            WhatsAppConnection.sidecar_session_key == key,
            ChannelAccount.provider == "whatsapp",
        )
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="whatsapp_baileys_connection_not_found",
        )
    return row


def _meta_connection(db: Session, connection_id: int) -> WhatsAppConnection:
    row = (
        db.query(WhatsAppConnection)
        .join(
            ChannelAccount,
            ChannelAccount.id == WhatsAppConnection.channel_account_id,
        )
        .filter(
            WhatsAppConnection.id == connection_id,
            WhatsAppConnection.transport == "meta_cloud_api",
            ChannelAccount.provider == "whatsapp",
        )
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="whatsapp_meta_connection_not_found",
        )
    return row


def _verify_baileys_payload(
    *,
    raw_body: bytes,
    connector_key: str | None,
    account_id: str | None,
    timestamp: str | None,
    signature: str | None,
) -> dict[str, Any]:
    try:
        verify_whatsapp_connector_headers(
            raw_body=raw_body,
            connector_key=connector_key,
            account_id=account_id,
            timestamp=timestamp,
            signature=signature,
        )
    except WhatsAppConnectorAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_whatsapp_connector_auth",
        ) from exc
    payload = _json_payload(raw_body)
    payload_account_id = str(payload.get("account_id") or account_id or "").strip()
    if not payload_account_id or payload_account_id != str(account_id or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="account_id_mismatch",
        )
    return payload


def _health(observed_state: str) -> str:
    if observed_state == "connected":
        return "healthy"
    if observed_state in {
        "auth_required",
        "qr_pending",
        "auth_persisting",
        "connecting",
        "degraded",
    }:
        return "degraded"
    if observed_state in {"logged_out", "error", "disabled"}:
        return "offline"
    return "unknown"


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if text:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return utc_now()


def _is_ignored_non_customer_chat(exc: WhatsAppInboundError) -> bool:
    return exc.args == (_IGNORED_NON_CUSTOMER_CHAT,)


@router.post("/baileys/inbound")
async def baileys_whatsapp_inbound(
    request: Request,
    x_nexus_connector_key: str | None = Header(
        default=None,
        alias="X-Nexus-Connector-Key",
    ),
    x_nexus_account_id: str | None = Header(
        default=None,
        alias="X-Nexus-Account-Id",
    ),
    x_nexus_timestamp: str | None = Header(
        default=None,
        alias="X-Nexus-Timestamp",
    ),
    x_nexus_signature: str | None = Header(
        default=None,
        alias="X-Nexus-Signature",
    ),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    raw_body = await request.body()
    payload = _verify_baileys_payload(
        raw_body=raw_body,
        connector_key=x_nexus_connector_key,
        account_id=x_nexus_account_id,
        timestamp=x_nexus_timestamp,
        signature=x_nexus_signature,
    )
    connection = _baileys_connection(db, x_nexus_account_id)
    normalized = dict(payload)
    normalized["account_id"] = connection.channel_account.account_id
    normalized["transport"] = "baileys_sidecar"
    try:
        with managed_session(db):
            result = ingest_whatsapp_inbound(db, normalized)
            db.flush()
    except WhatsAppInboundError as exc:
        if _is_ignored_non_customer_chat(exc):
            return {
                "ok": True,
                "ignored": True,
                "reason": _IGNORED_NON_CUSTOMER_CHAT,
            }
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_whatsapp_inbound",
        ) from exc
    return result.as_dict()


@router.post("/baileys/status")
async def baileys_whatsapp_status(
    request: Request,
    x_nexus_connector_key: str | None = Header(
        default=None,
        alias="X-Nexus-Connector-Key",
    ),
    x_nexus_account_id: str | None = Header(
        default=None,
        alias="X-Nexus-Account-Id",
    ),
    x_nexus_timestamp: str | None = Header(
        default=None,
        alias="X-Nexus-Timestamp",
    ),
    x_nexus_signature: str | None = Header(
        default=None,
        alias="X-Nexus-Signature",
    ),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    raw_body = await request.body()
    payload = _verify_baileys_payload(
        raw_body=raw_body,
        connector_key=x_nexus_connector_key,
        account_id=x_nexus_account_id,
        timestamp=x_nexus_timestamp,
        signature=x_nexus_signature,
    )
    connection = _baileys_connection(db, x_nexus_account_id)
    with managed_session(db):
        apply_observed_snapshot(connection, payload)
        connection.channel_account.health_status = _health(
            connection.observed_state
        )
        connection.channel_account.last_health_check_at = utc_now()
        db.flush()
    return {
        "ok": True,
        "connection_id": connection.id,
        "observed_state": connection.observed_state,
        "authentication_state": connection.authentication_state,
        "listener_state": connection.listener_state,
    }


@router.post("/baileys/delivery")
async def baileys_whatsapp_delivery(
    request: Request,
    x_nexus_connector_key: str | None = Header(
        default=None,
        alias="X-Nexus-Connector-Key",
    ),
    x_nexus_account_id: str | None = Header(
        default=None,
        alias="X-Nexus-Account-Id",
    ),
    x_nexus_timestamp: str | None = Header(
        default=None,
        alias="X-Nexus-Timestamp",
    ),
    x_nexus_signature: str | None = Header(
        default=None,
        alias="X-Nexus-Signature",
    ),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    raw_body = await request.body()
    payload = _verify_baileys_payload(
        raw_body=raw_body,
        connector_key=x_nexus_connector_key,
        account_id=x_nexus_account_id,
        timestamp=x_nexus_timestamp,
        signature=x_nexus_signature,
    )
    connection = _baileys_connection(db, x_nexus_account_id)
    metadata = (
        payload.get("metadata")
        if isinstance(payload.get("metadata"), dict)
        else {}
    )
    with managed_session(db):
        result = apply_whatsapp_delivery(
            db,
            connection=connection,
            provider_message_id=(
                str(payload.get("provider_message_id") or "").strip() or None
            ),
            status=str(payload.get("status") or "failed"),
            occurred_at=_parse_datetime(
                payload.get("occurred_at") or payload.get("sent_at")
            ),
            provider="baileys",
            receipt_id=(
                str(payload.get("idempotency_key") or "").strip() or None
            ),
            outbound_message_id=(
                int(metadata["outbound_message_id"])
                if metadata.get("outbound_message_id") is not None
                else None
            ),
            error_code=(
                str(payload.get("error_code") or "").strip() or None
            ),
            error_message=(
                str(payload.get("error_message") or "").strip() or None
            ),
            payload={
                key: payload.get(key)
                for key in (
                    "provider_message_id",
                    "status",
                    "sent_at",
                    "occurred_at",
                    "error_code",
                    "retryable",
                    "idempotency_key",
                )
                if payload.get(key) is not None
            },
        )
    return {"ok": True, **result.as_dict()}


@router.post("/baileys/desired-state")
async def baileys_whatsapp_desired_state(
    request: Request,
    x_nexus_connector_key: str | None = Header(
        default=None,
        alias="X-Nexus-Connector-Key",
    ),
    x_nexus_account_id: str | None = Header(
        default=None,
        alias="X-Nexus-Account-Id",
    ),
    x_nexus_timestamp: str | None = Header(
        default=None,
        alias="X-Nexus-Timestamp",
    ),
    x_nexus_signature: str | None = Header(
        default=None,
        alias="X-Nexus-Signature",
    ),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    raw_body = await request.body()
    payload = _verify_baileys_payload(
        raw_body=raw_body,
        connector_key=x_nexus_connector_key,
        account_id=x_nexus_account_id,
        timestamp=x_nexus_timestamp,
        signature=x_nexus_signature,
    )
    if payload.get("purpose") != "desired_state_reconciliation":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_reconciliation_purpose",
        )
    rows = (
        db.query(WhatsAppConnection)
        .join(
            ChannelAccount,
            ChannelAccount.id == WhatsAppConnection.channel_account_id,
        )
        .filter(
            WhatsAppConnection.transport == "baileys_sidecar",
            WhatsAppConnection.desired_state.in_(("binding", "active")),
            ChannelAccount.is_active.is_(True),
        )
        .order_by(WhatsAppConnection.id.asc())
        .all()
    )
    return {
        "ok": True,
        "accounts": [
            {
                "account_id": row.sidecar_session_key,
                "generation": row.desired_generation,
            }
            for row in rows
            if row.sidecar_session_key
        ],
    }


@router.get("/meta/{connection_id}/webhook")
def verify_meta_whatsapp_webhook(
    connection_id: int,
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(
        default=None,
        alias="hub.verify_token",
    ),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
    db: Session = Depends(get_db),
) -> Response:
    connection = _meta_connection(db, connection_id)
    expected = _crypto().decrypt(connection.verify_token_encrypted)
    if (
        hub_mode != "subscribe"
        or not expected
        or not hub_verify_token
        or not hmac.compare_digest(hub_verify_token, expected)
        or hub_challenge is None
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="meta_webhook_verification_failed",
        )
    return Response(
        content=hub_challenge,
        media_type="text/plain",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/meta/{connection_id}/webhook")
async def receive_meta_whatsapp_webhook(
    connection_id: int,
    request: Request,
    x_hub_signature_256: str | None = Header(
        default=None,
        alias="X-Hub-Signature-256",
    ),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    connection = _meta_connection(db, connection_id)
    app_secret = _crypto().decrypt(connection.app_secret_encrypted)
    if not app_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="meta_app_secret_missing",
        )
    raw_body = await request.body()
    try:
        verify_meta_webhook_signature(
            raw_body=raw_body,
            signature=x_hub_signature_256,
            app_secret=app_secret,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_meta_webhook_signature",
        ) from exc
    payload = _json_payload(raw_body)
    inbound_results: list[dict[str, Any]] = []
    delivery_results: list[dict[str, Any]] = []
    try:
        with managed_session(db):
            for message in iter_meta_inbound_messages(payload):
                if message.phone_number_id != connection.phone_number_id:
                    raise WhatsAppInboundError(
                        "meta_phone_number_scope_mismatch"
                    )
                sender_digits = "".join(
                    char for char in message.sender_phone if char.isdigit()
                )
                normalized = {
                    "transport": "meta_cloud_api",
                    "account_id": connection.channel_account.account_id,
                    "external_message_id": message.external_message_id,
                    "chat_jid": f"{sender_digits}@s.whatsapp.net",
                    "sender_jid": f"{sender_digits}@s.whatsapp.net",
                    "sender_phone": f"+{sender_digits}",
                    "sender_name": message.sender_name,
                    "message_type": message.message_type,
                    "body_text": message.body_text,
                    "received_at": message.received_at.isoformat(),
                    "from_me": False,
                    "projection_mode": "visitor",
                    "reply_to_message_id": message.reply_to_message_id,
                    "media_id": message.media_id,
                    "media_mime_type": message.media_mime_type,
                    "raw_message": message.raw_message,
                }
                inbound_results.append(
                    ingest_whatsapp_inbound(db, normalized).as_dict()
                )
            for event in iter_meta_delivery_events(payload):
                if event.phone_number_id != connection.phone_number_id:
                    raise WhatsAppInboundError(
                        "meta_phone_number_scope_mismatch"
                    )
                delivery_results.append(
                    apply_whatsapp_delivery(
                        db,
                        connection=connection,
                        provider_message_id=event.provider_message_id,
                        status=event.status,
                        occurred_at=event.occurred_at,
                        provider="meta",
                        receipt_id=(
                            event.conversation_id
                            or event.provider_message_id
                        ),
                        error_code=event.error_code,
                        error_message=event.error_message,
                        detail=event.pricing_category,
                        payload=event.raw_status,
                    ).as_dict()
                )
            connection.channel_account.health_status = _health(
                connection.observed_state
            )
            connection.channel_account.last_health_check_at = utc_now()
            db.flush()
    except (WhatsAppInboundError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_meta_whatsapp_webhook",
        ) from exc
    return {
        "ok": True,
        "inbound": inbound_results,
        "delivery": delivery_results,
    }
