from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session, joinedload

from ..models import TicketAttachment
from ..settings import get_settings
from .storage import StoredFile, get_storage_backend

settings = get_settings()
STORAGE = get_storage_backend()


class StoredUpload:
    def __init__(self, stored_name: str, storage_key: str, file_path: str | None, file_size: int, mime_type: str):
        self.stored_name = stored_name
        self.storage_key = storage_key
        self.file_path = file_path
        self.file_size = file_size
        self.mime_type = mime_type


def save_upload(file: UploadFile) -> StoredUpload:
    stored: StoredFile = STORAGE.save_upload(
        file,
        allowed_mime_types=set(settings.allowed_upload_mime_types),
        allowed_extensions=set(settings.allowed_upload_extensions),
        max_bytes=settings.max_upload_bytes,
    )
    return StoredUpload(
        stored_name=Path(file.filename or stored.storage_key).name,
        storage_key=stored.storage_key,
        file_path=str(stored.absolute_path) if stored.absolute_path is not None else None,
        file_size=stored.size_bytes,
        mime_type=stored.detected_mime_type,
    )


def build_attachment_download_url(attachment_id: int) -> str:
    return f"/api/files/{attachment_id}/download"


def get_attachment_or_404(db: Session, attachment_id: int) -> TicketAttachment:
    item = (
        db.query(TicketAttachment)
        .options(joinedload(TicketAttachment.ticket))
        .filter(TicketAttachment.id == attachment_id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail='Attachment not found')
    return item


def _resolve_attachment_path(attachment: TicketAttachment) -> Path:
    storage_key = attachment.storage_key or Path(attachment.file_path or '').name
    if not storage_key:
        raise HTTPException(status_code=404, detail='Attachment file is missing')
    return STORAGE.resolve(storage_key)


def download_attachment_response(attachment: TicketAttachment):
    media_type = attachment.mime_type or 'application/octet-stream'
    if attachment.storage_key:
        presigned = STORAGE.download_url(attachment.storage_key, filename=attachment.file_name, media_type=media_type)
        if presigned:
            return RedirectResponse(url=presigned, status_code=307)
    path = _resolve_attachment_path(attachment)
    return FileResponse(path=str(path), media_type=media_type, filename=attachment.file_name)
