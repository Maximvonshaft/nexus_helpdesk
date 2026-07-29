from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from ..models import TicketAttachment
from .storage import (
    LocalStorageBackend,
    S3CompatibleStorageBackend,
    get_storage_backend,
)
from .whatsapp_media_settings import (
    allowed_mime_types_for_kind,
    max_bytes_for_kind,
)


@dataclass(frozen=True)
class WhatsAppAttachmentBytes:
    content: bytes
    media_kind: str
    media_type: str
    filename: str


class WhatsAppAttachmentError(RuntimeError):
    pass


def load_whatsapp_attachment(
    attachment: TicketAttachment,
) -> WhatsAppAttachmentBytes:
    storage_key = str(attachment.storage_key or "").strip()
    if not storage_key:
        raise WhatsAppAttachmentError("whatsapp_attachment_storage_key_missing")
    media_type = str(attachment.mime_type or "application/octet-stream").split(";", 1)[0].strip().lower()
    media_kind = _media_kind(media_type)
    if media_type not in allowed_mime_types_for_kind(media_kind):
        raise WhatsAppAttachmentError("whatsapp_attachment_mime_not_allowed")
    max_bytes = max_bytes_for_kind(media_kind)
    declared_size = int(attachment.file_size or 0)
    if declared_size < 0 or declared_size > max_bytes:
        raise WhatsAppAttachmentError("whatsapp_attachment_size_invalid")
    backend = get_storage_backend()
    if isinstance(backend, LocalStorageBackend):
        path = backend.resolve(storage_key)
        content = _read_bounded_file(path, max_bytes)
    elif isinstance(backend, S3CompatibleStorageBackend):
        content = _read_bounded_s3(backend, storage_key, max_bytes)
    else:
        raise WhatsAppAttachmentError("whatsapp_attachment_storage_unsupported")
    if not content:
        raise WhatsAppAttachmentError("whatsapp_attachment_empty")
    if declared_size and declared_size != len(content):
        raise WhatsAppAttachmentError("whatsapp_attachment_size_mismatch")
    filename = Path(str(attachment.file_name or f"whatsapp-{attachment.id}").replace("\\", "/")).name[:255]
    return WhatsAppAttachmentBytes(
        content=content,
        media_kind=media_kind,
        media_type=media_type,
        filename=filename,
    )


def _read_bounded_file(path: Path, max_bytes: int) -> bytes:
    with path.open("rb") as handle:
        return _read_bounded_stream(handle, max_bytes)


def _read_bounded_s3(
    backend: S3CompatibleStorageBackend,
    storage_key: str,
    max_bytes: int,
) -> bytes:
    try:
        response = backend._client().get_object(  # noqa: SLF001 - centralized storage adapter boundary
            Bucket=backend.bucket,
            Key=storage_key,
        )
        body = response.get("Body")
        if body is None or not hasattr(body, "read"):
            raise WhatsAppAttachmentError("whatsapp_attachment_storage_response_invalid")
        try:
            return _read_bounded_stream(body, max_bytes)
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()
    except WhatsAppAttachmentError:
        raise
    except Exception as exc:
        raise WhatsAppAttachmentError("whatsapp_attachment_storage_read_failed") from exc


def _read_bounded_stream(stream: BinaryIO, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = stream.read(min(1024 * 1024, max_bytes + 1 - total))
        if not chunk:
            break
        if not isinstance(chunk, (bytes, bytearray)):
            raise WhatsAppAttachmentError("whatsapp_attachment_storage_response_invalid")
        total += len(chunk)
        if total > max_bytes:
            raise WhatsAppAttachmentError("whatsapp_attachment_size_invalid")
        chunks.append(bytes(chunk))
    return b"".join(chunks)


def _media_kind(media_type: str) -> str:
    if media_type in {"image/jpeg", "image/png"}:
        return "image"
    if media_type == "image/webp":
        return "sticker"
    if media_type.startswith("audio/"):
        return "audio"
    if media_type in {"video/mp4", "video/3gpp"}:
        return "video"
    if media_type in allowed_mime_types_for_kind("document"):
        return "document"
    raise WhatsAppAttachmentError("whatsapp_attachment_mime_not_supported")
