from __future__ import annotations

from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session

from ..models import ChannelAccount, WhatsAppInboundMessage
from ..models_whatsapp import WhatsAppConnection, WhatsAppMediaAsset


_INSTALLED = False
_MEDIA_KINDS = {"image", "video", "audio", "document", "sticker"}


def install_whatsapp_media_events() -> None:
    """Install one process-wide inbound-to-media projection contract."""

    global _INSTALLED
    if _INSTALLED:
        return
    event.listen(Session, "before_flush", _register_new_inbound_media)
    _INSTALLED = True


def _register_new_inbound_media(
    session: Session,
    _flush_context,
    _instances,
) -> None:
    candidates = [
        value
        for value in session.new
        if isinstance(value, WhatsAppInboundMessage)
    ]
    if not candidates:
        return
    for inbound in candidates:
        payload = (
            inbound.raw_payload_json
            if isinstance(inbound.raw_payload_json, dict)
            else {}
        )
        transport = _text(payload.get("transport"), 40)
        kind = _media_kind(
            payload.get("media_kind") or inbound.message_type
        )
        media_id = _provider_media_id(
            transport=transport,
            payload=payload,
            inbound=inbound,
        )
        if not kind or not media_id:
            continue
        provider = "meta" if transport == "meta_cloud_api" else "baileys"
        with session.no_autoflush:
            connection = (
                session.query(WhatsAppConnection)
                .join(
                    ChannelAccount,
                    ChannelAccount.id
                    == WhatsAppConnection.channel_account_id,
                )
                .filter(
                    WhatsAppConnection.channel_account_id
                    == inbound.channel_account_id,
                    ChannelAccount.provider == "whatsapp",
                )
                .first()
            )
        if connection is None:
            raise RuntimeError("whatsapp_media_connection_missing")
        duplicate_in_session = any(
            isinstance(value, WhatsAppMediaAsset)
            and value.connection_id == connection.id
            and value.provider == provider
            and value.provider_media_id == media_id
            for value in session.new
        )
        if duplicate_in_session:
            continue
        session.add(
            WhatsAppMediaAsset(
                tenant_id=connection.tenant_id,
                connection_id=connection.id,
                inbound_message=inbound,
                provider=provider,
                provider_media_id=media_id,
                media_kind=kind,
                file_name=_text(payload.get("media_filename"), 255),
                declared_mime_type=_mime(payload.get("media_mime_type")),
                storage_status="pending",
                scan_status="pending",
                attempt_count=0,
                max_attempts=5,
            )
        )


def _provider_media_id(
    *,
    transport: str | None,
    payload: dict[str, Any],
    inbound: WhatsAppInboundMessage,
) -> str | None:
    if transport == "meta_cloud_api":
        return _text(payload.get("media_id"), 255)
    if transport == "baileys_sidecar":
        return _text(inbound.external_message_id, 255)
    return None


def _media_kind(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    if normalized.endswith("message"):
        normalized = normalized[: -len("message")]
    return normalized if normalized in _MEDIA_KINDS else None


def _mime(value: Any) -> str | None:
    normalized = str(value or "").split(";", 1)[0].strip().lower()
    return normalized[:160] or None


def _text(value: Any, limit: int) -> str | None:
    normalized = str(value or "").strip()
    return normalized[:limit] or None
