from __future__ import annotations

import hashlib
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ChannelAccount, WhatsAppInboundMessage
from ..models_whatsapp import WhatsAppConnection
from ..services.whatsapp_inbound import (
    WhatsAppConnectorAuthError,
    verify_whatsapp_connector_headers,
)
from ..services.whatsapp_media_service import (
    WhatsAppMediaError,
    get_or_create_inbound_media_asset,
    persist_inbound_media_bytes,
)
from ..services.whatsapp_media_settings import max_bytes_for_kind
from ..unit_of_work import managed_session


router = APIRouter(
    prefix="/api/integrations/whatsapp/baileys",
    tags=["whatsapp-media-integration"],
)


def _connection(db: Session, session_key: str | None) -> WhatsAppConnection:
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


@router.post("/media")
async def receive_baileys_media(
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
    x_nexus_message_id: str | None = Header(
        default=None,
        alias="X-Nexus-Message-Id",
    ),
    x_nexus_media_kind: str | None = Header(
        default=None,
        alias="X-Nexus-Media-Kind",
    ),
    x_nexus_media_type: str | None = Header(
        default=None,
        alias="X-Nexus-Media-Type",
    ),
    x_nexus_media_filename: str | None = Header(
        default=None,
        alias="X-Nexus-Media-Filename",
    ),
    x_nexus_media_sha256: str | None = Header(
        default=None,
        alias="X-Nexus-Media-Sha256",
    ),
    db: Session = Depends(get_db),
) -> dict:
    message_id = str(x_nexus_message_id or "").strip()
    media_kind = str(x_nexus_media_kind or "").strip().lower()
    media_type = str(x_nexus_media_type or "").strip().lower()
    if not message_id or len(message_id) > 180:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_media_message_id",
        )
    try:
        limit = max_bytes_for_kind(media_kind)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="unsupported_whatsapp_media_kind",
        ) from exc
    content_length = int(request.headers.get("content-length") or 0)
    if content_length <= 0 or content_length > limit:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="whatsapp_media_size_invalid",
        )
    raw_body = await request.body()
    if len(raw_body) != content_length or len(raw_body) > limit:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="whatsapp_media_size_invalid",
        )
    try:
        verify_whatsapp_connector_headers(
            raw_body=raw_body,
            connector_key=x_nexus_connector_key,
            account_id=x_nexus_account_id,
            timestamp=x_nexus_timestamp,
            signature=x_nexus_signature,
        )
    except WhatsAppConnectorAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_whatsapp_connector_auth",
        ) from exc
    connection = _connection(db, x_nexus_account_id)
    inbound = (
        db.query(WhatsAppInboundMessage)
        .filter(
            WhatsAppInboundMessage.channel_account_id
            == connection.channel_account_id,
            WhatsAppInboundMessage.external_message_id == message_id,
        )
        .first()
    )
    if inbound is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="whatsapp_inbound_message_not_ready",
        )
    observed_sha256 = hashlib.sha256(raw_body).hexdigest()
    if (
        x_nexus_media_sha256
        and not secrets_compare_digest(
            x_nexus_media_sha256.strip().lower(),
            observed_sha256,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="whatsapp_media_sha256_mismatch",
        )
    try:
        with managed_session(db):
            asset = get_or_create_inbound_media_asset(
                db,
                inbound=inbound,
                provider="baileys",
                provider_media_id=message_id,
                media_kind=media_kind,
                declared_mime_type=media_type,
                file_name=(
                    unquote(x_nexus_media_filename)[:255]
                    if x_nexus_media_filename
                    else None
                ),
            )
            stored = persist_inbound_media_bytes(
                db,
                asset=asset,
                content=raw_body,
                declared_mime_type=media_type,
                file_name=(
                    unquote(x_nexus_media_filename)[:255]
                    if x_nexus_media_filename
                    else None
                ),
                expected_sha256=observed_sha256,
            )
            db.flush()
    except WhatsAppMediaError as exc:
        raise HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
                if exc.retryable
                else status.HTTP_400_BAD_REQUEST
            ),
            detail={"error_code": exc.code, "retryable": exc.retryable},
        ) from exc
    return {
        "ok": True,
        "asset_id": stored.asset_id,
        "attachment_id": stored.attachment_id,
        "sha256": stored.sha256,
        "byte_size": stored.byte_size,
        "mime_type": stored.mime_type,
    }


def secrets_compare_digest(first: str, second: str) -> bool:
    import hmac

    return hmac.compare_digest(first, second)
