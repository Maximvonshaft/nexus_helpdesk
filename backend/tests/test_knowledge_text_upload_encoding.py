from __future__ import annotations

from io import BytesIO
from pathlib import Path
import zipfile

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from app.services.knowledge_document_service import parse_document_bytes
from app.services.storage import LocalStorageBackend
from app.services.text_decoding import decode_text_upload, is_supported_text_upload


SAMPLE_TEXT = "中文知识库：客户可以在发货前申请修改地址。\nDo not invent parcel status."
DOCX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _upload_file(filename: str, content: bytes, content_type: str = "text/plain") -> UploadFile:
    return UploadFile(filename=filename, file=BytesIO(content), headers=Headers({"content-type": content_type}))


def _docx_bytes(*paragraphs: str) -> bytes:
    body = "".join(
        "<w:p><w:r><w:t>" + paragraph + "</w:t></w:r></w:p>"
        for paragraph in paragraphs
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body>{body}</w:body>'
        '</w:document>'
    )
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "")
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


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


def test_parse_document_bytes_accepts_markdown_upload():
    content = b"# Delivery SLA\n\n- Nigeria sea freight: 15 days\n- Nigeria air freight: 10 days"
    body, normalized = parse_document_bytes(content=content, filename="shipping.md", mime_type="text/markdown")
    assert "Delivery SLA" in body
    assert "Nigeria sea freight" in normalized


def test_parse_document_bytes_accepts_docx_upload():
    content = _docx_bytes("尼日利亚海运时效为 15 天。", "Do not invent parcel status.")
    body, normalized = parse_document_bytes(content=content, filename="shipping.docx", mime_type=DOCX_MIME_TYPE)
    assert "尼日利亚海运时效为 15 天。" in body
    assert "Do not invent parcel status" in normalized


def test_parse_document_bytes_rejects_legacy_doc_upload():
    with pytest.raises(HTTPException) as exc:
        parse_document_bytes(content=b"legacy-doc", filename="legacy.doc", mime_type="application/msword")
    assert exc.value.status_code == 400
    assert ".doc" in str(exc.value.detail)
    assert ".docx" in str(exc.value.detail)


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


def test_local_storage_save_upload_accepts_markdown_for_knowledge_documents(tmp_path: Path):
    backend = LocalStorageBackend(tmp_path)
    content = b"# Knowledge\n\nMarkdown upload works."
    stored = backend.save_upload(
        _upload_file("knowledge.md", content, "text/markdown"),
        allowed_mime_types={"text/plain", "text/markdown"},
        allowed_extensions={".txt", ".md", ".markdown"},
        max_bytes=1024 * 1024,
    )
    assert stored.detected_mime_type == "text/markdown"
    assert stored.absolute_path is not None
    assert stored.absolute_path.read_bytes() == content


def test_local_storage_save_upload_accepts_docx_for_knowledge_documents(tmp_path: Path):
    backend = LocalStorageBackend(tmp_path)
    content = _docx_bytes("Knowledge DOCX upload works.")
    stored = backend.save_upload(
        _upload_file("knowledge.docx", content, DOCX_MIME_TYPE),
        allowed_mime_types={DOCX_MIME_TYPE},
        allowed_extensions={".docx"},
        max_bytes=1024 * 1024,
    )
    assert stored.detected_mime_type == DOCX_MIME_TYPE
    assert stored.absolute_path is not None
    assert stored.absolute_path.read_bytes() == content


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
        _upload_file("knowledge.txt", content),
        allowed_mime_types={"text/plain"},
        allowed_extensions={".txt"},
        max_bytes=1024 * 1024,
    )

    assert stored.detected_mime_type == "text/plain"
    assert stored.size_bytes == len(content)
    assert stored.absolute_path is not None
    assert stored.absolute_path.exists()
    assert stored.absolute_path.read_bytes() == content
