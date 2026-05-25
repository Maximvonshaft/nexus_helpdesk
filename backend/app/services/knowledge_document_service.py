from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path

from fastapi import HTTPException, UploadFile

from ..settings import get_settings
from .text_decoding import TextDecodingError, decode_text_upload

SUPPORTED_DOCUMENT_MIME_TYPES = {"text/plain", "application/pdf"}
SUPPORTED_DOCUMENT_EXTENSIONS = {".txt", ".pdf"}


def normalize_document_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def read_upload_bytes(file: UploadFile) -> bytes:
    settings = get_settings()
    try:
        file.file.seek(0)
    except Exception:
        pass
    content = file.file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="Uploaded knowledge document exceeds MAX_UPLOAD_BYTES")
    try:
        file.file.seek(0)
    except Exception:
        pass
    return content


def parse_document_bytes(*, content: bytes, filename: str | None, mime_type: str | None) -> tuple[str, str]:
    extension = Path(filename or "").suffix.lower()
    normalized_mime = (mime_type or "application/octet-stream").split(";", 1)[0].strip().lower()
    if extension not in SUPPORTED_DOCUMENT_EXTENSIONS and normalized_mime not in SUPPORTED_DOCUMENT_MIME_TYPES:
        raise HTTPException(status_code=400, detail="Knowledge upload supports text and PDF documents only")

    if normalized_mime == "application/pdf" or extension == ".pdf":
        text = _extract_pdf_text(content)
    else:
        text = _extract_plain_text(content)

    normalized = normalize_document_text(text)
    if not normalized:
        raise HTTPException(status_code=400, detail="Parsed knowledge document is empty")
    return text.strip(), normalized


def _extract_plain_text(content: bytes) -> str:
    try:
        return decode_text_upload(content)
    except TextDecodingError as exc:
        raise HTTPException(status_code=400, detail="Uploaded text file must be encoded as UTF-8, UTF-16, GB18030, or GBK") from exc


def _extract_pdf_text(content: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="PDF text extraction dependency is not installed") from exc

    try:
        reader = PdfReader(BytesIO(content))
        return "\n\n".join((page.extract_text() or "").strip() for page in reader.pages).strip()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Unable to extract text from PDF knowledge document") from exc
