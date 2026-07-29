from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services import knowledge_document_service as service
from app.services.knowledge_archive_safety import KnowledgeArchiveLimits


class _Page:
    def __init__(self, text: str) -> None:
        self.text = text

    def extract_text(self) -> str:
        return self.text


class _Reader:
    pages = []
    is_encrypted = False

    def __init__(self, _buffer, strict=False) -> None:  # noqa: ANN001
        del strict
        self.pages = list(type(self).pages)
        self.is_encrypted = type(self).is_encrypted


def _install_reader(monkeypatch, pages, *, encrypted=False) -> None:  # noqa: ANN001
    _Reader.pages = list(pages)
    _Reader.is_encrypted = encrypted
    monkeypatch.setitem(sys.modules, "pypdf", SimpleNamespace(PdfReader=_Reader))


def test_pdf_page_budget_is_enforced_before_page_extraction(monkeypatch) -> None:
    calls = 0

    class CountingPage(_Page):
        def extract_text(self) -> str:
            nonlocal calls
            calls += 1
            return super().extract_text()

    _install_reader(
        monkeypatch,
        [CountingPage("x") for _ in range(service.PDF_MAX_PAGES + 1)],
    )
    with pytest.raises(HTTPException) as exc:
        service._extract_pdf_text(b"%PDF-bounded")
    assert exc.value.status_code == 400
    assert exc.value.detail == "Knowledge PDF exceeds the page extraction budget"
    assert calls == 0


def test_pdf_output_budget_is_enforced_incrementally(monkeypatch) -> None:
    _install_reader(monkeypatch, [_Page("a" * 60), _Page("b" * 60)])
    monkeypatch.setattr(
        service,
        "archive_limits_for_upload",
        lambda _maximum: KnowledgeArchiveLimits(
            max_members=10,
            max_member_bytes=1000,
            max_total_expanded_bytes=1000,
            max_extracted_text_chars=100,
        ),
    )
    with pytest.raises(HTTPException) as exc:
        service._extract_pdf_text(b"%PDF-output")
    assert exc.value.status_code == 400
    assert exc.value.detail == "Knowledge document text exceeds the extraction budget"


def test_pdf_time_budget_terminates_between_pages(monkeypatch) -> None:
    _install_reader(monkeypatch, [_Page("first"), _Page("second")])
    observed = iter([0.0, 1.0, service.PDF_EXTRACTION_TIMEOUT_SECONDS + 1.0])
    monkeypatch.setattr(service.time, "monotonic", lambda: next(observed))
    with pytest.raises(HTTPException) as exc:
        service._extract_pdf_text(b"%PDF-time")
    assert exc.value.status_code == 408
    assert exc.value.detail == "Knowledge PDF extraction exceeded the time budget"


def test_encrypted_pdf_is_rejected(monkeypatch) -> None:
    _install_reader(monkeypatch, [_Page("secret")], encrypted=True)
    with pytest.raises(HTTPException) as exc:
        service._extract_pdf_text(b"%PDF-encrypted")
    assert exc.value.status_code == 400
    assert exc.value.detail == "Encrypted Knowledge PDF documents are not supported"
