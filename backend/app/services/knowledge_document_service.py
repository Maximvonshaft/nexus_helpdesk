from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from io import BytesIO
from pathlib import Path

from fastapi import HTTPException, UploadFile

from ..settings import get_settings
from .text_decoding import TextDecodingError, decode_text_upload

DOCX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MARKDOWN_MIME_TYPES = {"text/markdown", "text/x-markdown"}
TEXT_DOCUMENT_MIME_TYPES = {"text/plain", *MARKDOWN_MIME_TYPES}
SUPPORTED_DOCUMENT_MIME_TYPES = {"application/pdf", DOCX_MIME_TYPE, *TEXT_DOCUMENT_MIME_TYPES}
SUPPORTED_DOCUMENT_EXTENSIONS = {".txt", ".md", ".markdown", ".pdf", ".docx"}
_DOCX_TEXT_XML_LIMIT_BYTES = 8 * 1024 * 1024
_DOCX_TEXT_PARTS = {
    "word/document.xml",
    "word/footnotes.xml",
    "word/endnotes.xml",
}


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
        raise HTTPException(status_code=400, detail="Knowledge upload supports text, Markdown, PDF, and DOCX documents only")
    if extension == ".doc":
        raise HTTPException(status_code=400, detail="Legacy .doc knowledge uploads are not supported. Please convert the file to .docx, PDF, Markdown, or plain text.")

    if normalized_mime == "application/pdf" or extension == ".pdf":
        text = _extract_pdf_text(content)
    elif normalized_mime == DOCX_MIME_TYPE or extension == ".docx":
        text = _extract_docx_text(content)
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
        raise HTTPException(status_code=400, detail="Uploaded text or Markdown file must be encoded as UTF-8, UTF-16, GB18030, or GBK") from exc


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


def _extract_docx_text(content: bytes) -> str:
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            names = set(archive.namelist())
            if "word/document.xml" not in names:
                raise HTTPException(status_code=400, detail="DOCX knowledge document is missing word/document.xml")
            text_parts: list[str] = []
            for name in sorted(_DOCX_TEXT_PARTS & names):
                info = archive.getinfo(name)
                if info.file_size > _DOCX_TEXT_XML_LIMIT_BYTES:
                    raise HTTPException(status_code=400, detail="DOCX knowledge document XML part is too large")
                xml_bytes = archive.read(name)
                text = _extract_docx_xml_text(xml_bytes)
                if text:
                    text_parts.append(text)
            return "\n\n".join(text_parts).strip()
    except HTTPException:
        raise
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Uploaded DOCX knowledge document is not a valid DOCX file") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Unable to extract text from DOCX knowledge document") from exc


def _extract_docx_xml_text(xml_bytes: bytes) -> str:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise HTTPException(status_code=400, detail="Unable to parse DOCX document XML") from exc

    paragraphs: list[str] = []
    current: list[str] = []
    for elem in root.iter():
        tag = elem.tag.rsplit("}", 1)[-1]
        if tag == "t" and elem.text:
            current.append(elem.text)
        elif tag == "tab":
            current.append("\t")
        elif tag == "br":
            current.append("\n")
        elif tag == "p":
            paragraph = "".join(current).strip()
            if paragraph:
                paragraphs.append(paragraph)
            current = []
    tail = "".join(current).strip()
    if tail:
        paragraphs.append(tail)
    return "\n".join(paragraphs)
