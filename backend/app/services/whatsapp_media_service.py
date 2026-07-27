from __future__ import annotations

import hashlib
import ipaddress
import socket
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
from sqlalchemy.orm import Session

from ..enums import NoteVisibility
from ..models import ChannelAccount, TicketAttachment, WhatsAppInboundMessage
from ..models_whatsapp import WhatsAppConnection, WhatsAppMediaAsset
from ..utils.time import utc_now
from .secret_crypto import SecretCryptoService
from .storage import get_storage_backend
from .whatsapp_media_scanner import MediaScanError, scan_whatsapp_media
from .whatsapp_media_settings import (
    allowed_mime_types_for_kind,
    get_whatsapp_media_settings,
    max_bytes_for_kind,
)


_ALLOWED_META_MEDIA_HOST_SUFFIXES = (
    ".facebook.com",
    ".fbcdn.net",
    ".fbsbx.com",
    ".whatsapp.net",
)

_EXTENSION_BY_MIME = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "audio/aac": ".aac",
    "audio/amr": ".amr",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "video/mp4": ".mp4",
    "video/3gpp": ".3gp",
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "text/plain": ".txt",
    "text/csv": ".csv",
}


class MediaHttpClient(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> Any:
        ...


class WhatsAppMediaError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class StoredWhatsAppMedia:
    asset_id: int
    storage_key: str
    attachment_id: int | None
    sha256: str
    byte_size: int
    mime_type: str


def get_or_create_inbound_media_asset(
    db: Session,
    *,
    inbound: WhatsAppInboundMessage,
    provider: str,
    provider_media_id: str,
    media_kind: str,
    declared_mime_type: str | None,
    file_name: str | None = None,
) -> WhatsAppMediaAsset:
    connection = (
        db.query(WhatsAppConnection)
        .join(
            ChannelAccount,
            ChannelAccount.id == WhatsAppConnection.channel_account_id,
        )
        .filter(
            WhatsAppConnection.channel_account_id == inbound.channel_account_id,
            ChannelAccount.provider == "whatsapp",
        )
        .first()
    )
    if connection is None:
        raise WhatsAppMediaError("whatsapp_media_connection_missing")
    normalized_provider = str(provider or "").strip().lower()
    if normalized_provider not in {"meta", "baileys"}:
        raise WhatsAppMediaError("whatsapp_media_provider_invalid")
    normalized_media_id = str(provider_media_id or "").strip()
    if not normalized_media_id or len(normalized_media_id) > 255:
        raise WhatsAppMediaError("whatsapp_media_id_invalid")
    normalized_kind = str(media_kind or "").strip().lower()
    max_bytes_for_kind(normalized_kind)
    existing = (
        db.query(WhatsAppMediaAsset)
        .filter(
            WhatsAppMediaAsset.connection_id == connection.id,
            WhatsAppMediaAsset.provider == normalized_provider,
            WhatsAppMediaAsset.provider_media_id == normalized_media_id,
        )
        .first()
    )
    if existing is not None:
        if existing.inbound_message_id not in {None, inbound.id}:
            raise WhatsAppMediaError("whatsapp_media_identity_conflict")
        existing.inbound_message_id = inbound.id
        return existing
    row = WhatsAppMediaAsset(
        tenant_id=connection.tenant_id,
        connection_id=connection.id,
        inbound_message_id=inbound.id,
        provider=normalized_provider,
        provider_media_id=normalized_media_id,
        media_kind=normalized_kind,
        file_name=_safe_filename(file_name, declared_mime_type, normalized_kind),
        declared_mime_type=_normalized_mime(declared_mime_type),
        storage_status="pending",
        scan_status="pending",
    )
    db.add(row)
    db.flush()
    return row


def persist_inbound_media_bytes(
    db: Session,
    *,
    asset: WhatsAppMediaAsset,
    content: bytes,
    declared_mime_type: str | None,
    file_name: str | None = None,
    expected_sha256: str | None = None,
) -> StoredWhatsAppMedia:
    settings = get_whatsapp_media_settings()
    if not settings.enabled:
        raise WhatsAppMediaError("whatsapp_media_disabled")
    if asset.storage_status == "available" and asset.storage_key and asset.sha256:
        return StoredWhatsAppMedia(
            asset_id=asset.id,
            storage_key=asset.storage_key,
            attachment_id=asset.ticket_attachment_id,
            sha256=asset.sha256,
            byte_size=int(asset.byte_size or 0),
            mime_type=asset.detected_mime_type or asset.declared_mime_type or "application/octet-stream",
        )
    max_bytes = max_bytes_for_kind(asset.media_kind)
    if not content or len(content) > max_bytes:
        _fail_asset(asset, "whatsapp_media_size_invalid", rejected=True)
        raise WhatsAppMediaError("whatsapp_media_size_invalid")
    mime_type = _normalized_mime(declared_mime_type or asset.declared_mime_type)
    allowed_mimes = allowed_mime_types_for_kind(asset.media_kind)
    if mime_type not in allowed_mimes:
        _fail_asset(asset, "whatsapp_media_mime_not_allowed", rejected=True)
        raise WhatsAppMediaError("whatsapp_media_mime_not_allowed")
    digest = hashlib.sha256(content).hexdigest()
    if expected_sha256 and not _matches_sha256(expected_sha256, digest):
        _fail_asset(asset, "whatsapp_media_sha256_mismatch", rejected=True)
        raise WhatsAppMediaError("whatsapp_media_sha256_mismatch")

    asset.storage_status = "scanning"
    asset.scan_status = "pending"
    asset.updated_at = utc_now()
    db.flush()
    try:
        scan = scan_whatsapp_media(content)
    except MediaScanError as exc:
        _fail_asset(asset, str(exc), scan_unavailable=True)
        raise WhatsAppMediaError(str(exc), retryable=True) from exc
    asset.scanned_at = utc_now()
    if scan.status != "clean":
        asset.storage_status = "quarantined"
        asset.scan_status = "infected"
        asset.last_error_code = "whatsapp_media_malware_detected"
        asset.last_error_message = scan.signature or "malware_detected"
        asset.updated_at = utc_now()
        db.flush()
        raise WhatsAppMediaError("whatsapp_media_malware_detected")

    filename = _safe_filename(file_name or asset.file_name, mime_type, asset.media_kind)
    suffix = Path(filename).suffix.lower()
    storage = get_storage_backend()
    stored = storage.persist_bytes(
        content=content,
        filename=filename,
        media_type=mime_type,
        allowed_mime_types=allowed_mimes,
        allowed_extensions={suffix},
        max_bytes=max_bytes,
    )
    attachment_id = _project_ticket_attachment(
        db,
        asset=asset,
        filename=filename,
        storage_key=stored.storage_key,
        mime_type=stored.detected_mime_type,
        size_bytes=stored.size_bytes,
    )
    asset.file_name = filename
    asset.declared_mime_type = mime_type
    asset.detected_mime_type = stored.detected_mime_type
    asset.byte_size = stored.size_bytes
    asset.sha256 = digest
    asset.storage_key = stored.storage_key
    asset.ticket_attachment_id = attachment_id
    asset.storage_status = "available"
    asset.scan_status = "clean"
    asset.downloaded_at = asset.downloaded_at or utc_now()
    asset.available_at = utc_now()
    asset.last_error_code = None
    asset.last_error_message = None
    asset.updated_at = utc_now()
    db.flush()
    return StoredWhatsAppMedia(
        asset_id=asset.id,
        storage_key=stored.storage_key,
        attachment_id=attachment_id,
        sha256=digest,
        byte_size=stored.size_bytes,
        mime_type=stored.detected_mime_type,
    )


def download_and_persist_meta_media(
    db: Session,
    *,
    asset: WhatsAppMediaAsset,
    client: MediaHttpClient | None = None,
) -> StoredWhatsAppMedia:
    if asset.provider != "meta":
        raise WhatsAppMediaError("meta_media_asset_required")
    connection = asset.connection
    if connection.transport != "meta_cloud_api":
        raise WhatsAppMediaError("meta_media_connection_required")
    access_token = SecretCryptoService.whatsapp().decrypt(
        connection.access_token_encrypted
    )
    if not access_token or not connection.graph_api_version:
        raise WhatsAppMediaError("meta_media_credentials_missing")
    active_client = client or httpx.Client(
        follow_redirects=False,
        trust_env=False,
    )
    close_client = client is None
    try:
        asset.storage_status = "downloading"
        asset.updated_at = utc_now()
        db.flush()
        metadata_response = active_client.get(
            f"https://graph.facebook.com/{connection.graph_api_version}/{asset.provider_media_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15.0,
        )
        metadata = _response_json(metadata_response, "meta_media_metadata_failed")
        media_url = str(metadata.get("url") or "").strip()
        mime_type = _normalized_mime(
            metadata.get("mime_type") or asset.declared_mime_type
        )
        if not media_url:
            raise WhatsAppMediaError("meta_media_url_missing", retryable=True)
        _validate_meta_media_url(media_url)
        asset.provider_url_expires_at = utc_now() + timedelta(minutes=5)
        media_response = active_client.get(
            media_url,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30.0,
        )
        status_code = int(getattr(media_response, "status_code", 0) or 0)
        if status_code != 200:
            raise WhatsAppMediaError("meta_media_download_failed", retryable=True)
        content = bytes(getattr(media_response, "content", b""))
        asset.downloaded_at = utc_now()
        return persist_inbound_media_bytes(
            db,
            asset=asset,
            content=content,
            declared_mime_type=mime_type,
            file_name=asset.file_name,
            expected_sha256=str(metadata.get("sha256") or "").strip() or None,
        )
    except WhatsAppMediaError:
        raise
    except httpx.HTTPError as exc:
        _fail_asset(asset, "meta_media_transport_error")
        raise WhatsAppMediaError("meta_media_transport_error", retryable=True) from exc
    finally:
        if close_client and hasattr(active_client, "close"):
            active_client.close()


def _project_ticket_attachment(
    db: Session,
    *,
    asset: WhatsAppMediaAsset,
    filename: str,
    storage_key: str,
    mime_type: str,
    size_bytes: int,
) -> int | None:
    inbound = asset.inbound_message
    if inbound is None or inbound.ticket_id is None:
        return None
    if asset.ticket_attachment_id is not None:
        return asset.ticket_attachment_id
    attachment = TicketAttachment(
        ticket_id=inbound.ticket_id,
        uploaded_by=None,
        file_name=filename,
        storage_key=storage_key,
        file_path=None,
        file_url=None,
        mime_type=mime_type,
        file_size=size_bytes,
        visibility=NoteVisibility.external,
        created_at=utc_now(),
    )
    db.add(attachment)
    db.flush()
    return attachment.id


def _response_json(response: Any, failure_code: str) -> dict[str, Any]:
    status_code = int(getattr(response, "status_code", 0) or 0)
    if status_code != 200:
        raise WhatsAppMediaError(failure_code, retryable=status_code >= 500 or status_code == 429)
    try:
        payload = response.json()
    except Exception as exc:
        raise WhatsAppMediaError(failure_code, retryable=True) from exc
    if not isinstance(payload, dict):
        raise WhatsAppMediaError(failure_code, retryable=True)
    return payload


def _validate_meta_media_url(value: str) -> None:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
        or not any(host.endswith(suffix) for suffix in _ALLOWED_META_MEDIA_HOST_SUFFIXES)
    ):
        raise WhatsAppMediaError("meta_media_url_not_allowed")
    try:
        addresses = {
            info[4][0]
            for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        }
    except OSError as exc:
        raise WhatsAppMediaError("meta_media_dns_failed", retryable=True) from exc
    if not addresses:
        raise WhatsAppMediaError("meta_media_dns_failed", retryable=True)
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise WhatsAppMediaError("meta_media_url_not_allowed")


def _safe_filename(
    value: str | None,
    mime_type: str | None,
    media_kind: str,
) -> str:
    candidate = Path(str(value or "").replace("\\", "/")).name.strip()
    normalized_mime = _normalized_mime(mime_type)
    suffix = _EXTENSION_BY_MIME.get(normalized_mime, ".bin")
    if not candidate or candidate in {".", ".."}:
        candidate = f"whatsapp-{media_kind}{suffix}"
    if len(candidate) > 200:
        candidate = candidate[-200:]
    if not Path(candidate).suffix:
        candidate += suffix
    return candidate


def _normalized_mime(value: Any) -> str:
    return str(value or "application/octet-stream").split(";", 1)[0].strip().lower()


def _matches_sha256(expected: str, observed_hex: str) -> bool:
    normalized = expected.strip().lower()
    if len(normalized) == 64 and all(char in "0123456789abcdef" for char in normalized):
        return normalized == observed_hex
    return True


def _fail_asset(
    asset: WhatsAppMediaAsset,
    code: str,
    *,
    rejected: bool = False,
    scan_unavailable: bool = False,
) -> None:
    asset.storage_status = "rejected" if rejected else "failed"
    asset.scan_status = "unavailable" if scan_unavailable else "failed"
    asset.last_error_code = code[:120]
    asset.last_error_message = code[:500]
    asset.updated_at = utc_now()
