from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ..models import TicketAttachment, TicketOutboundMessage
from .storage_read_service import StorageReadError, read_storage_bytes
from .whatsapp_media_settings import (
    allowed_mime_types_for_kind,
    max_bytes_for_kind,
)


@dataclass(frozen=True)
class WhatsAppOutboundMedia:
    attachment_id: int
    kind: str
    mime_type: str
    filename: str
    caption: str | None
    content: bytes
    sha256: str


class WhatsAppOutboundMediaError(RuntimeError):
    pass


def resolve_whatsapp_outbound_media(
    message: TicketOutboundMessage,
) -> WhatsAppOutboundMedia | None:
    attachments = list(message.attachments)
    if not attachments:
        return None
    if len(attachments) != 1:
        raise WhatsAppOutboundMediaError(
            "whatsapp_one_attachment_per_message_required"
        )
    attachment = attachments[0]
    kind, mime_type = _kind_and_mime(attachment)
    max_bytes = max_bytes_for_kind(kind)
    declared_size = int(attachment.file_size or 0)
    if declared_size <= 0 or declared_size > max_bytes:
        raise WhatsAppOutboundMediaError("whatsapp_attachment_size_invalid")
    storage_key = str(attachment.storage_key or "").strip()
    if not storage_key:
        raise WhatsAppOutboundMediaError("whatsapp_attachment_storage_key_missing")
    try:
        content = read_storage_bytes(storage_key, max_bytes=max_bytes)
    except StorageReadError as exc:
        code = exc.args[0] if exc.args else "whatsapp_attachment_read_failed"
        raise WhatsAppOutboundMediaError(str(code)) from exc
    if len(content) != declared_size:
        raise WhatsAppOutboundMediaError("whatsapp_attachment_size_mismatch")
    filename = _safe_filename(attachment.file_name, mime_type, kind)
    caption = str(message.body or "").strip()[:1024] or None
    if kind in {"audio", "sticker"}:
        caption = None
    return WhatsAppOutboundMedia(
        attachment_id=int(attachment.id),
        kind=kind,
        mime_type=mime_type,
        filename=filename,
        caption=caption,
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _kind_and_mime(attachment: TicketAttachment) -> tuple[str, str]:
    mime_type = str(attachment.mime_type or "").split(";", 1)[0].strip().lower()
    if mime_type in {"image/jpeg", "image/png", "image/webp"}:
        kind = "image"
    elif mime_type in {"video/mp4", "video/3gpp"}:
        kind = "video"
    elif mime_type in {
        "audio/aac",
        "audio/amr",
        "audio/mpeg",
        "audio/mp4",
        "audio/ogg",
        "audio/opus",
    }:
        kind = "audio"
    elif mime_type in allowed_mime_types_for_kind("document"):
        kind = "document"
    else:
        raise WhatsAppOutboundMediaError("whatsapp_attachment_mime_not_supported")
    if mime_type not in allowed_mime_types_for_kind(kind):
        raise WhatsAppOutboundMediaError("whatsapp_attachment_mime_not_supported")
    return kind, mime_type


def _safe_filename(value: str | None, mime_type: str, kind: str) -> str:
    filename = Path(str(value or "").replace("\\", "/")).name.strip()
    if filename and filename not in {".", ".."}:
        return filename[-200:]
    extension = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "video/mp4": ".mp4",
        "video/3gpp": ".3gp",
        "audio/aac": ".aac",
        "audio/amr": ".amr",
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/ogg": ".ogg",
        "audio/opus": ".opus",
        "application/pdf": ".pdf",
        "text/plain": ".txt",
        "text/csv": ".csv",
    }.get(mime_type, ".bin")
    return f"whatsapp-{kind}{extension}"
