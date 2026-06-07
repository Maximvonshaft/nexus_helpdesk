from __future__ import annotations

import csv
import re
import zipfile
from dataclasses import dataclass, field
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
_LABEL_RE = re.compile(r"^\s*([^:：]{1,32})\s*[:：]\s*(.+?)\s*$")
_CH_WAYBILL_RE = re.compile(r"\bCH\b|CH\s*(?:开头|prefix|starts?)", re.I)


@dataclass(frozen=True)
class DocumentKnowledgeExtraction:
    title: str | None = None
    summary: str | None = None
    knowledge_kind: str = "document"
    fact_question: str | None = None
    fact_answer: str | None = None
    fact_aliases: list[str] = field(default_factory=list)
    answer_mode: str = "guided_answer"
    risk_flags: list[str] = field(default_factory=list)
    confidence: float = 0.0
    extractor: str = "deterministic_template_v1"


def normalize_document_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def extract_knowledge_candidate(*, text: str, normalized_text: str | None = None, filename: str | None = None) -> DocumentKnowledgeExtraction:
    labels = _labeled_sections(text)
    normalized = (normalized_text or normalize_document_text(text)).strip()
    title = labels.get("title") or _title_from_text(text) or _title_from_filename(filename)
    question = labels.get("question")
    answer = labels.get("answer") or labels.get("rule") or labels.get("handling")
    aliases = _split_aliases(labels.get("keywords") or labels.get("aliases"))
    risk_flags: list[str] = []
    confidence = 0.0

    ch_candidate = _extract_ch_waybill_rule(normalized)
    if ch_candidate:
        title = title if title and title != _title_from_filename(filename) else ch_candidate.title
        question = question or ch_candidate.fact_question
        answer = answer or ch_candidate.fact_answer
        aliases = _dedupe_aliases([*aliases, *ch_candidate.fact_aliases])
        risk_flags = _dedupe_aliases([*risk_flags, *ch_candidate.risk_flags])
        confidence = max(confidence, ch_candidate.confidence)

    if labels:
        confidence = max(confidence, 0.72)
    if question and answer:
        confidence = max(confidence, 0.86)

    if not answer:
        return DocumentKnowledgeExtraction(title=title, summary=_summary_from_text(normalized), confidence=confidence)

    if not question:
        question = f"{title or 'Knowledge rule'} 如何处理？"

    summary = _summary_from_text(answer or normalized)
    return DocumentKnowledgeExtraction(
        title=title,
        summary=summary,
        knowledge_kind="business_fact",
        fact_question=question,
        fact_answer=answer,
        fact_aliases=_dedupe_aliases(aliases),
        answer_mode="guided_answer",
        risk_flags=risk_flags or ["human_review_required"],
        confidence=confidence,
    )


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


def _labeled_sections(text: str) -> dict[str, str]:
    aliases = {
        "标题": "title",
        "title": "title",
        "名称": "title",
        "问题": "question",
        "客户问题": "question",
        "question": "question",
        "faq": "question",
        "答案": "answer",
        "答复": "answer",
        "回复": "answer",
        "answer": "answer",
        "规则": "rule",
        "rule": "rule",
        "处理方式": "handling",
        "异常处理": "handling",
        "sop": "handling",
        "关键词": "keywords",
        "关键字": "keywords",
        "别名": "aliases",
        "aliases": "aliases",
        "keywords": "keywords",
    }
    sections: dict[str, str] = {}
    current_key: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _LABEL_RE.match(line)
        if match:
            label = match.group(1).strip().lower()
            key = aliases.get(label)
            if key:
                sections[key] = match.group(2).strip()
                current_key = key
                continue
        if current_key in {"answer", "rule", "handling"}:
            sections[current_key] = f"{sections[current_key]}\n{line}".strip()
    return sections


def _extract_ch_waybill_rule(normalized: str) -> DocumentKnowledgeExtraction | None:
    lowered = normalized.lower()
    has_ch_rule = bool(_CH_WAYBILL_RE.search(normalized) and ("12" in normalized) and ("digit" in lowered or "数字" in normalized or "位" in normalized))
    has_no_status_guard = any(term in lowered for term in ("不得判断", "不要判断", "cannot infer", "do not infer", "no trusted", "无可信"))
    has_tracking_failure = any(term in lowered for term in ("查不到", "not found", "wrong tracking", "核对", "多输", "少输", "输错"))
    if not has_ch_rule and not (has_tracking_failure and ("waybill" in lowered or "运单" in normalized or "单号" in normalized)):
        return None

    answer_parts = ["请客户核对运单号是否完整；瑞士 Speedaf 运单号通常为 CH 开头，后接 12 位数字。"]
    if has_no_status_guard or has_tracking_failure:
        answer_parts.append("在没有可信查单结果时，不得判断或编造物流状态；请客户重新发送正确单号后再查询。")
    return DocumentKnowledgeExtraction(
        title="瑞士 Speedaf 运单号格式与输错提醒",
        summary="瑞士 CH 运单号格式和查不到时的核对提醒。",
        knowledge_kind="business_fact",
        fact_question="客户输入瑞士 Speedaf 运单号查不到怎么办？",
        fact_answer="".join(answer_parts),
        fact_aliases=[
            "CH运单号格式",
            "瑞士运单号格式",
            "订单号输错",
            "多输一个0",
            "少输数字",
            "运单号查不到",
            "waybill not found",
            "wrong tracking number",
            "CH tracking number format",
        ],
        answer_mode="guided_answer",
        risk_flags=["contains_tracking_sop", "not_live_status", "human_review_required"],
        confidence=0.92,
    )


def _title_from_text(text: str) -> str | None:
    for line in text.splitlines():
        cleaned = line.strip(" \t\r\n#*")
        if 4 <= len(cleaned) <= 120:
            return cleaned
    return None


def _title_from_filename(filename: str | None) -> str | None:
    stem = Path(filename or "").stem.strip()
    return stem or None


def _summary_from_text(text: str | None) -> str | None:
    normalized = normalize_document_text(text)
    if not normalized:
        return None
    sentence = re.split(r"(?<=[。！？.!?])\s+", normalized, maxsplit=1)[0].strip() or normalized
    return sentence if len(sentence) <= 360 else f"{sentence[:357].rstrip()}..."


def _split_aliases(value: str | None) -> list[str]:
    if not value:
        return []
    return _dedupe_aliases(part for part in re.split(r"[,，;；、\n]+", value) if part.strip())


def _dedupe_aliases(values) -> list[str]:  # noqa: ANN001
    seen: set[str] = set()
    items: list[str] = []
    for value in values:
        cleaned = str(value or "").strip()
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        items.append(cleaned)
        if len(items) >= 30:
            break
    return items


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
