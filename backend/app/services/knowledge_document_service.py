from __future__ import annotations

import csv
import re
import zipfile
from html.parser import HTMLParser
from io import BytesIO
from io import StringIO
from pathlib import Path
from xml.etree import ElementTree

from fastapi import HTTPException, UploadFile

from ..settings import get_settings
from .text_decoding import TextDecodingError, decode_text_upload

TEXT_DOCUMENT_EXTENSIONS = {".txt", ".md", ".markdown", ".csv", ".html", ".htm"}
SUPPORTED_DOCUMENT_MIME_TYPES = {
    "text/plain",
    "text/markdown",
    "text/csv",
    "text/html",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
SUPPORTED_DOCUMENT_EXTENSIONS = {*TEXT_DOCUMENT_EXTENSIONS, ".pdf", ".docx", ".xlsx"}
_DOCX_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_XLSX_NAMESPACE = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


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
        raise HTTPException(status_code=400, detail="Knowledge upload supports TXT, PDF, DOCX, XLSX, CSV, Markdown, and HTML documents only")

    if normalized_mime == "application/pdf" or extension == ".pdf":
        text = _extract_pdf_text(content)
    elif extension == ".docx" or normalized_mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        text = _extract_docx_text(content)
    elif extension == ".xlsx" or normalized_mime == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        text = _extract_xlsx_text(content)
    elif extension == ".csv" or normalized_mime == "text/csv":
        text = _extract_csv_text(content)
    elif extension in {".html", ".htm"} or normalized_mime == "text/html":
        text = _extract_html_text(content)
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


def _extract_csv_text(content: bytes) -> str:
    text = _extract_plain_text(content)
    try:
        rows = []
        for row in csv.reader(StringIO(text)):
            cells = [cell.strip() for cell in row if cell and cell.strip()]
            if cells:
                rows.append(" | ".join(cells))
        return "\n".join(rows) or text
    except csv.Error:
        return text


class _ReadableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag in {"script", "style"}:
            self._skip_depth += 1
        if tag in {"p", "br", "div", "section", "article", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip_depth:
            self._skip_depth -= 1
        if tag in {"p", "div", "section", "article", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        cleaned = data.strip()
        if cleaned:
            self.parts.append(cleaned)


def _extract_html_text(content: bytes) -> str:
    parser = _ReadableHTMLParser()
    parser.feed(_extract_plain_text(content))
    parser.close()
    return "\n".join(part for part in parser.parts if part.strip())


def _extract_docx_text(content: bytes) -> str:
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            names = archive.namelist()
            xml_names = [
                "word/document.xml",
                *sorted(name for name in names if name.startswith("word/header") and name.endswith(".xml")),
                *sorted(name for name in names if name.startswith("word/footer") and name.endswith(".xml")),
            ]
            return "\n\n".join(_extract_word_xml_text(_read_zip_member(archive, name)) for name in xml_names if name in names).strip()
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Unable to extract text from DOCX knowledge document") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Unable to extract text from DOCX knowledge document") from exc


def _extract_word_xml_text(xml_bytes: bytes) -> str:
    root = ElementTree.fromstring(xml_bytes)
    paragraph_tag = f"{{{_DOCX_NAMESPACE}}}p"
    text_tag = f"{{{_DOCX_NAMESPACE}}}t"
    tab_tag = f"{{{_DOCX_NAMESPACE}}}tab"
    break_tag = f"{{{_DOCX_NAMESPACE}}}br"
    carriage_return_tag = f"{{{_DOCX_NAMESPACE}}}cr"
    paragraphs: list[str] = []
    for paragraph in root.iter(paragraph_tag):
        parts: list[str] = []
        for node in paragraph.iter():
            if node.tag == text_tag and node.text:
                parts.append(node.text)
            elif node.tag == tab_tag:
                parts.append(" ")
            elif node.tag in {break_tag, carriage_return_tag}:
                parts.append("\n")
        text = "".join(parts).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def _extract_xlsx_text(content: bytes) -> str:
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            names = archive.namelist()
            shared_strings = _xlsx_shared_strings(archive, names)
            sheet_names = sorted(name for name in names if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"))
            rows: list[str] = []
            for sheet_name in sheet_names:
                rows.extend(_extract_xlsx_sheet_rows(_read_zip_member(archive, sheet_name), shared_strings))
            return "\n".join(rows).strip()
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Unable to extract text from XLSX knowledge document") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Unable to extract text from XLSX knowledge document") from exc


def _xlsx_shared_strings(archive: zipfile.ZipFile, names: list[str]) -> list[str]:
    if "xl/sharedStrings.xml" not in names:
        return []
    root = ElementTree.fromstring(_read_zip_member(archive, "xl/sharedStrings.xml"))
    text_tag = f"{{{_XLSX_NAMESPACE}}}t"
    values: list[str] = []
    for item in root:
        text = "".join(node.text or "" for node in item.iter(text_tag)).strip()
        values.append(text)
    return values


def _extract_xlsx_sheet_rows(xml_bytes: bytes, shared_strings: list[str]) -> list[str]:
    root = ElementTree.fromstring(xml_bytes)
    row_tag = f"{{{_XLSX_NAMESPACE}}}row"
    cell_tag = f"{{{_XLSX_NAMESPACE}}}c"
    value_tag = f"{{{_XLSX_NAMESPACE}}}v"
    text_tag = f"{{{_XLSX_NAMESPACE}}}t"
    rows: list[str] = []
    for row in root.iter(row_tag):
        cells: list[str] = []
        for cell in row.iter(cell_tag):
            value = ""
            cell_type = cell.attrib.get("t")
            raw_value = cell.find(value_tag)
            if cell_type == "s" and raw_value is not None and raw_value.text:
                index = int(raw_value.text)
                value = shared_strings[index] if 0 <= index < len(shared_strings) else ""
            elif cell_type == "inlineStr":
                value = "".join(node.text or "" for node in cell.iter(text_tag)).strip()
            elif raw_value is not None and raw_value.text:
                value = raw_value.text.strip()
            if value:
                cells.append(value)
        if cells:
            rows.append(" | ".join(cells))
    return rows


def _read_zip_member(archive: zipfile.ZipFile, name: str) -> bytes:
    info = archive.getinfo(name)
    if info.file_size > get_settings().max_upload_bytes:
        raise HTTPException(status_code=400, detail="Knowledge document contains an extracted file that is too large")
    return archive.read(name)
