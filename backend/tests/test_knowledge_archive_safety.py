from __future__ import annotations

import stat
import zipfile
from io import BytesIO

import pytest
from fastapi import HTTPException

from app.services.knowledge_archive_safety import (
    KnowledgeArchiveLimits,
    SafeKnowledgeArchive,
    enforce_extracted_text_budget,
    parse_bounded_xml,
)
from app.services.knowledge_document_service import _extract_docx_text, _extract_xlsx_text


def _zip(entries: list[tuple[str, bytes, zipfile.ZipInfo | None]], *, compression=zipfile.ZIP_DEFLATED) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
        for name, payload, info in entries:
            if info is None:
                archive.writestr(name, payload)
            else:
                archive.writestr(info, payload)
    return buffer.getvalue()


def _limits(**overrides) -> KnowledgeArchiveLimits:
    values = {
        "max_members": 8,
        "max_member_bytes": 4096,
        "max_total_expanded_bytes": 8192,
        "max_compression_ratio": 100.0,
        "max_xml_bytes": 4096,
        "max_xml_depth": 8,
        "max_xml_nodes": 64,
        "max_extracted_text_chars": 1000,
    }
    values.update(overrides)
    return KnowledgeArchiveLimits(**values)


def test_archive_rejects_path_traversal_and_symlink() -> None:
    traversal = _zip([("../word/document.xml", b"<root/>", None)])
    with pytest.raises(HTTPException, match="path"):
        SafeKnowledgeArchive(traversal, limits=_limits())

    link_info = zipfile.ZipInfo("word/document.xml")
    link_info.external_attr = (stat.S_IFLNK | 0o777) << 16
    symlink = _zip([("word/document.xml", b"target", link_info)])
    with pytest.raises(HTTPException, match="links"):
        SafeKnowledgeArchive(symlink, limits=_limits())


def test_archive_rejects_member_count_total_size_and_ratio() -> None:
    many = _zip([(f"word/{index}.xml", b"<x/>", None) for index in range(3)])
    with pytest.raises(HTTPException, match="member count"):
        SafeKnowledgeArchive(many, limits=_limits(max_members=2))

    total = _zip(
        [
            ("word/a.xml", b"a" * 3000, None),
            ("word/b.xml", b"b" * 3000, None),
        ],
        compression=zipfile.ZIP_STORED,
    )
    with pytest.raises(HTTPException, match="total extraction"):
        SafeKnowledgeArchive(total, limits=_limits(max_total_expanded_bytes=4096))

    bomb = _zip([("word/document.xml", b"A" * 4096, None)])
    with pytest.raises(HTTPException, match="compression ratio"):
        SafeKnowledgeArchive(bomb, limits=_limits(max_compression_ratio=2.0))


def test_archive_read_is_bounded_and_exact() -> None:
    content = _zip([("word/document.xml", b"<root>safe</root>", None)])
    with SafeKnowledgeArchive(content, limits=_limits()) as archive:
        assert archive.names == ("word/document.xml",)
        assert archive.read("word/document.xml", xml=True) == b"<root>safe</root>"
        with pytest.raises(HTTPException, match="missing"):
            archive.read("word/missing.xml", xml=True)


def test_xml_parser_rejects_doctype_depth_and_node_budgets() -> None:
    with pytest.raises(HTTPException, match="declarations"):
        parse_bounded_xml(b"<!DOCTYPE x [<!ENTITY y 'z'>]><x>&y;</x>", limits=_limits())

    deep = ("<x>" * 10 + "value" + "</x>" * 10).encode()
    with pytest.raises(HTTPException, match="depth"):
        parse_bounded_xml(deep, limits=_limits(max_xml_depth=5))

    wide = ("<root>" + "<x/>" * 70 + "</root>").encode()
    with pytest.raises(HTTPException, match="node budget"):
        parse_bounded_xml(wide, limits=_limits(max_xml_nodes=32))


def test_text_budget_fails_closed() -> None:
    assert enforce_extracted_text_budget("safe", limits=_limits()) == "safe"
    with pytest.raises(HTTPException, match="text"):
        enforce_extracted_text_budget("x" * 1001, limits=_limits())


def test_valid_docx_and_xlsx_still_extract_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.knowledge_document_service.get_settings",
        lambda: type("Settings", (), {"max_upload_bytes": 1024 * 1024})(),
    )
    docx_xml = b'''<?xml version="1.0" encoding="UTF-8"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body><w:p><w:r><w:t>Hello DOCX</w:t></w:r></w:p></w:body>
    </w:document>'''
    docx = _zip([("word/document.xml", docx_xml, None)])
    assert _extract_docx_text(docx) == "Hello DOCX"

    shared = b'''<?xml version="1.0" encoding="UTF-8"?>
    <sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
      <si><t>Hello XLSX</t></si>
    </sst>'''
    sheet = b'''<?xml version="1.0" encoding="UTF-8"?>
    <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
      <sheetData><row><c t="s"><v>0</v></c></row></sheetData>
    </worksheet>'''
    xlsx = _zip([
        ("xl/sharedStrings.xml", shared, None),
        ("xl/worksheets/sheet1.xml", sheet, None),
    ])
    assert _extract_xlsx_text(xlsx) == "Hello XLSX"
