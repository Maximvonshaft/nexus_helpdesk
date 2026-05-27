from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from app.services.knowledge_document_service import parse_document_bytes
from app.services.storage import LocalStorageBackend
from app.services.text_decoding import decode_text_upload, is_supported_text_upload


SAMPLE_TEXT = "中文知识库：客户可以在发货前申请修改地址。\nDo not invent parcel status."


def _upload_file(filename: str, content: bytes, content_type: str = "text/plain") -> UploadFile:
    return UploadFile(filename=filename, file=BytesIO(content), headers=Headers({"content-type": content_type}))


@pytest.mark.parametrize(
    ("encoding", "content"),
    [
        ("utf-8", SAMPLE_TEXT.encode("utf-8")),
        ("utf-8-sig", SAMPLE_TEXT.encode("utf-8-sig")),
        ("gb18030", SAMPLE_TEXT.encode("gb18030")),
        ("gbk", SAMPLE_TEXT.encode("gbk")),
        ("utf-16", SAMPLE_TEXT.encode("utf-16")),
        ("utf-16-le", SAMPLE_TEXT.encode("utf-16-le")),
    ],
)
def test_decode_text_upload_accepts_common_windows_text_encodings(encoding: str, content: bytes):
    decoded = decode_text_upload(content)
    assert "中文知识库" in decoded
    assert "Do not invent parcel status" in decoded
    assert is_supported_text_upload(content), encoding


@pytest.mark.parametrize(
    "content",
    [
        SAMPLE_TEXT.encode("utf-8"),
        SAMPLE_TEXT.encode("utf-8-sig"),
        SAMPLE_TEXT.encode("gb18030"),
        SAMPLE_TEXT.encode("gbk"),
        SAMPLE_TEXT.encode("utf-16"),
    ],
)
def test_parse_document_bytes_accepts_common_text_encodings(content: bytes):
    body, normalized = parse_document_bytes(content=content, filename="knowledge.txt", mime_type="text/plain")
    assert "中文知识库" in body
    assert "修改地址" in normalized


def test_parse_document_bytes_accepts_markdown_csv_and_html():
    markdown_body, markdown_normalized = parse_document_bytes(
        content="# Address policy\nCustomers can update delivery details before dispatch.".encode("utf-8"),
        filename="policy.md",
        mime_type="text/markdown",
    )
    csv_body, csv_normalized = parse_document_bytes(
        content="Question,Answer\nPOD,Proof of delivery\nAddress,Before dispatch only".encode("utf-8"),
        filename="faq.csv",
        mime_type="text/csv",
    )
    html_body, html_normalized = parse_document_bytes(
        content="<h1>Customer FAQ</h1><script>ignore()</script><p>POD means proof of delivery.</p>".encode("utf-8"),
        filename="faq.html",
        mime_type="text/html",
    )

    assert "Address policy" in markdown_body
    assert "delivery details" in markdown_normalized
    assert "POD | Proof of delivery" in csv_body
    assert "Before dispatch only" in csv_normalized
    assert "Customer FAQ" in html_body
    assert "ignore" not in html_normalized


def test_parse_document_bytes_accepts_docx_and_xlsx():
    docx = BytesIO()
    with zipfile.ZipFile(docx, "w") as archive:
        archive.writestr(
            "word/document.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
              <w:body>
                <w:p><w:r><w:t>Customer support policy</w:t></w:r></w:p>
                <w:p><w:r><w:t>Do not invent parcel status.</w:t></w:r></w:p>
              </w:body>
            </w:document>""",
        )

    xlsx = BytesIO()
    with zipfile.ZipFile(xlsx, "w") as archive:
        archive.writestr(
            "xl/sharedStrings.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <si><t>Question</t></si>
              <si><t>Answer</t></si>
              <si><t>POD</t></si>
              <si><t>Proof of delivery</t></si>
            </sst>""",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <sheetData>
                <row><c t="s"><v>0</v></c><c t="s"><v>1</v></c></row>
                <row><c t="s"><v>2</v></c><c t="s"><v>3</v></c></row>
              </sheetData>
            </worksheet>""",
        )

    docx_body, docx_normalized = parse_document_bytes(
        content=docx.getvalue(),
        filename="policy.docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    xlsx_body, xlsx_normalized = parse_document_bytes(
        content=xlsx.getvalue(),
        filename="faq.xlsx",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    assert "Customer support policy" in docx_body
    assert "Do not invent parcel status" in docx_normalized
    assert "Question | Answer" in xlsx_body
    assert "POD | Proof of delivery" in xlsx_normalized


def test_parse_document_bytes_rejects_binary_txt():
    with pytest.raises(HTTPException) as exc:
        parse_document_bytes(content=bytes([0, 1, 2, 3]) * 128, filename="binary.txt", mime_type="text/plain")
    assert exc.value.status_code == 400
    assert "UTF-8" in str(exc.value.detail)
    assert "GBK" in str(exc.value.detail)


@pytest.mark.parametrize(
    "content",
    [
        SAMPLE_TEXT.encode("utf-8"),
        SAMPLE_TEXT.encode("gb18030"),
        SAMPLE_TEXT.encode("gbk"),
        SAMPLE_TEXT.encode("utf-16"),
    ],
)
def test_local_storage_save_upload_accepts_common_text_encodings(tmp_path: Path, content: bytes):
    backend = LocalStorageBackend(tmp_path)
    stored = backend.save_upload(
        _upload_file("knowledge.txt", content),
        allowed_mime_types={"text/plain"},
        allowed_extensions={".txt"},
        max_bytes=1024 * 1024,
    )
    assert stored.detected_mime_type == "text/plain"
    assert stored.size_bytes == len(content)
    assert stored.absolute_path is not None
    assert stored.absolute_path.exists()


@pytest.mark.parametrize(
    ("filename", "content_type", "expected_mime"),
    [
        ("knowledge.md", "text/markdown", "text/markdown"),
        ("knowledge.csv", "text/csv", "text/csv"),
        ("knowledge.html", "text/html", "text/html"),
    ],
)
def test_local_storage_save_upload_accepts_business_text_documents(tmp_path: Path, filename: str, content_type: str, expected_mime: str):
    backend = LocalStorageBackend(tmp_path)
    stored = backend.save_upload(
        _upload_file(filename, SAMPLE_TEXT.encode("utf-8"), content_type),
        allowed_mime_types={"text/plain", "text/markdown", "text/csv", "text/html"},
        allowed_extensions={".txt", ".md", ".csv", ".html"},
        max_bytes=1024 * 1024,
    )

    assert stored.detected_mime_type == expected_mime
    assert stored.size_bytes == len(SAMPLE_TEXT.encode("utf-8"))


def test_local_storage_save_upload_rejects_binary_txt(tmp_path: Path):
    backend = LocalStorageBackend(tmp_path)
    with pytest.raises(HTTPException) as exc:
        backend.save_upload(
            _upload_file("binary.txt", bytes([0, 1, 2, 3]) * 128),
            allowed_mime_types={"text/plain"},
            allowed_extensions={".txt"},
            max_bytes=1024 * 1024,
        )
    assert exc.value.status_code == 400


def test_is_supported_text_upload_accepts_utf8_sample_cut_mid_multibyte_character():
    content = ("规" * 1366).encode("utf-8")
    sample = content[:4096]
    with pytest.raises(UnicodeDecodeError):
        sample.decode("utf-8")
    assert is_supported_text_upload(sample)


def test_local_storage_save_upload_accepts_utf8_when_sniff_sample_cuts_multibyte(tmp_path: Path):
    text = ("规" * 1366) + "\nDo not invent parcel status."
    content = text.encode("utf-8")
    sample = content[:4096]
    with pytest.raises(UnicodeDecodeError):
        sample.decode("utf-8")

    backend = LocalStorageBackend(tmp_path)
    stored = backend.save_upload(
        _upload_file("rules.txt", content),
        allowed_mime_types={"text/plain"},
        allowed_extensions={".txt"},
        max_bytes=1024 * 1024,
    )

    assert stored.detected_mime_type == "text/plain"
    assert stored.size_bytes == len(content)
    assert stored.absolute_path is not None
    assert stored.absolute_path.exists()
    assert stored.absolute_path.read_bytes() == content
