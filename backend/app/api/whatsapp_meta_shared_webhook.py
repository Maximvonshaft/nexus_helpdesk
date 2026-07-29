from __future__ import annotations

import hmac
import json
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ChannelAccount
from ..models_whatsapp import WhatsAppConnection
from ..services.secret_crypto import SecretCryptoService
from ..services.whatsapp_delivery import apply_whatsapp_delivery
from ..services.whatsapp_inbound import WhatsAppInboundError, ingest_whatsapp_inbound
from ..services.whatsapp_meta_webhook import (
    MetaDeliveryEvent,
    MetaInboundMessage,
    iter_meta_delivery_events,
    iter_meta_inbound_messages,
    verify_meta_webhook_signature,
)
from ..unit_of_work import managed_session
from ..utils.time import utc_now


router = APIRouter(
    prefix="/api/integrations/whatsapp/meta",
    tags=["whatsapp-integration"],
)

_MAX_SHARED_META_CONNECTIONS = 1000


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


def _meta_connections_query(db: Session):
    return (
        db.query(WhatsAppConnection)
        .join(
            ChannelAccount,
            ChannelAccount.id == WhatsAppConnection.channel_account_id,
        )
        .filter(
            WhatsAppConnection.transport == "meta_cloud_api",
            ChannelAccount.provider == "whatsapp",
        )
    )


def _payload_waba_ids(payload: dict[str, Any]) -> set[str]:
    entries = payload.get("entry") if isinstance(payload.get("entry"), list) else []
    result: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        value = str(entry.get("id") or "").strip()
        if value:
            result.add(value)
    return result


def _candidate_connections(
    db: Session,
    *,
    phone_number_ids: set[str],
    waba_ids: set[str],
) -> list[WhatsAppConnection]:
    predicates = []
    if phone_number_ids:
        predicates.append(WhatsAppConnection.phone_number_id.in_(phone_number_ids))
    if waba_ids:
        predicates.append(WhatsAppConnection.waba_id.in_(waba_ids))
    if not predicates:
        return []
    return (
        _meta_connections_query(db)
        .filter(or_(*predicates))
        .order_by(WhatsAppConnection.id.asc())
        .limit(_MAX_SHARED_META_CONNECTIONS)
        .all()
    )


def _matching_signature_secret(
    *,
    raw_body: bytes,
    signature: str | None,
    connections: list[WhatsAppConnection],
) -> str:
    crypto = _crypto()
    secrets: set[str] = set()
    for connection in connections:
        secret = crypto.decrypt(connection.app_secret_encrypted)
        if secret:
            secrets.add(secret)
    matches: list[str] = []
    for secret in sorted(secrets):
        try:
            verify_meta_webhook_signature(
                raw_body=raw_body,
                signature=signature,
                app_secret=secret,
            )
        except ValueError:
            continue
        matches.append(secret)
    if len(matches) != 1:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_meta_webhook_signature",
        )
    return matches[0]


def _phone_connection_map(
    connections: list[WhatsAppConnection],
    *,
    verified_secret: str,
) -> dict[str, WhatsAppConnection]:
    crypto = _crypto()
    result: dict[str, WhatsAppConnection] = {}
    for connection in connections:
        phone_number_id = str(connection.phone_number_id or "").strip()
        if not phone_number_id:
            continue
        secret = crypto.decrypt(connection.app_secret_encrypted)
        if not secret or not hmac.compare_digest(secret, verified_secret):
            continue
        if phone_number_id in result and result[phone_number_id].id != connection.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="duplicate_meta_phone_number_connection",
            )
        result[phone_number_id] = connection
    return result


def _connection_for_phone(
    mapping: dict[str, WhatsAppConnection],
    phone_number_id: str,
) -> WhatsAppConnection:
    connection = mapping.get(phone_number_id)
    if connection is None:
        raise WhatsAppInboundError("meta_phone_number_connection_missing")
    return connection


def _normalized_inbound(
    connection: WhatsAppConnection,
    message: MetaInboundMessage,
) -> dict[str, Any]:
    sender_digits = "".join(char for char in message.sender_phone if char.isdigit())
    return {
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


@router.get("/webhook")
def verify_shared_meta_whatsapp_webhook(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
    db: Session = Depends(get_db),
) -> Response:
    supplied = str(hub_verify_token or "").strip()
    if hub_mode != "subscribe" or not supplied or hub_challenge is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="meta_webhook_verification_failed",
        )
    crypto = _crypto()
    matched = False
    rows = (
        _meta_connections_query(db)
        .filter(WhatsAppConnection.verify_token_encrypted.is_not(None))
        .order_by(WhatsAppConnection.id.asc())
        .limit(_MAX_SHARED_META_CONNECTIONS)
        .all()
    )
    for connection in rows:
        expected = crypto.decrypt(connection.verify_token_encrypted)
        if expected and hmac.compare_digest(supplied, expected):
            matched = True
    if not matched:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="meta_webhook_verification_failed",
        )
    return Response(
        content=hub_challenge,
        media_type="text/plain",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/webhook")
async def receive_shared_meta_whatsapp_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(
        default=None,
        alias="X-Hub-Signature-256",
    ),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    raw_body = await request.body()
    payload = _json_payload(raw_body)
    try:
        inbound_messages = list(iter_meta_inbound_messages(payload))
        delivery_events = list(iter_meta_delivery_events(payload))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_meta_whatsapp_webhook",
        ) from exc

    phone_number_ids = {
        item.phone_number_id for item in [*inbound_messages, *delivery_events]
    }
    connections = _candidate_connections(
        db,
        phone_number_ids=phone_number_ids,
        waba_ids=_payload_waba_ids(payload),
    )
    if not connections:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="whatsapp_meta_connection_not_found",
        )
    verified_secret = _matching_signature_secret(
        raw_body=raw_body,
        signature=x_hub_signature_256,
        connections=connections,
    )
    by_phone = _phone_connection_map(
        connections,
        verified_secret=verified_secret,
    )

    inbound_results: list[dict[str, Any]] = []
    delivery_results: list[dict[str, Any]] = []
    touched: dict[int, WhatsAppConnection] = {}
    try:
        with managed_session(db):
            for message in inbound_messages:
                connection = _connection_for_phone(by_phone, message.phone_number_id)
                inbound_results.append(
                    ingest_whatsapp_inbound(
                        db,
                        _normalized_inbound(connection, message),
                    ).as_dict()
                )
                touched[connection.id] = connection
            for event in delivery_events:
                connection = _connection_for_phone(by_phone, event.phone_number_id)
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
                touched[connection.id] = connection
            for connection in touched.values():
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
