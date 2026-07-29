from __future__ import annotations

from io import BytesIO

from fastapi import HTTPException

from ..settings import get_settings
from .knowledge_archive_safety import (
    archive_limits_for_upload,
    enforce_extracted_text_budget,
)

_MAX_PDF_PAGES = 500
_MAX_PAGE_TEXT_CHARS = 200_000
_INSTALLED = False


def extract_pdf_text_bounded(content: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="PDF text extraction dependency is not installed",
        ) from exc

    limits = archive_limits_for_upload(get_settings().max_upload_bytes)
    try:
        reader = PdfReader(BytesIO(content), strict=True)
        if reader.is_encrypted:
            raise HTTPException(
                status_code=400,
                detail="Encrypted PDF knowledge documents are not supported",
            )
        page_count = len(reader.pages)
        if page_count < 1 or page_count > _MAX_PDF_PAGES:
            raise HTTPException(
                status_code=400,
                detail="PDF knowledge document exceeds the page extraction budget",
            )

        chunks: list[str] = []
        total_chars = 0
        for page_number, page in enumerate(reader.pages, start=1):
            try:
                text = (page.extract_text() or "").strip()
            except Exception as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unable to extract PDF knowledge page {page_number}",
                ) from exc
            if len(text) > _MAX_PAGE_TEXT_CHARS:
                raise HTTPException(
                    status_code=400,
                    detail="PDF knowledge page exceeds the text extraction budget",
                )
            if not text:
                continue
            total_chars += len(text)
            if total_chars > limits.max_extracted_text_chars:
                raise HTTPException(
                    status_code=400,
                    detail="PDF knowledge document exceeds the text extraction budget",
                )
            chunks.append(text)
        return enforce_extracted_text_budget(
            "\n\n".join(chunks).strip(),
            limits=limits,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="Unable to extract text from PDF knowledge document",
        ) from exc


def install_knowledge_pdf_safety() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from . import knowledge_document_service

    knowledge_document_service._extract_pdf_text = extract_pdf_text_bounded
    _INSTALLED = True


__all__ = ["extract_pdf_text_bounded", "install_knowledge_pdf_safety"]
