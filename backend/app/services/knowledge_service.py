from __future__ import annotations

import re
from typing import Optional

from fastapi import HTTPException, UploadFile
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models_control_plane import KnowledgeItem, KnowledgeItemVersion
from ..utils.time import utc_now
from . import file_service
from .knowledge_document_service import parse_document_bytes, read_upload_bytes
from .knowledge_retrieval_service import index_published_item

VALID_STATUSES = {"draft", "active", "archived"}
VALID_SOURCE_TYPES = {"text", "url", "file"}
VALID_KNOWLEDGE_KINDS = {"document", "faq", "business_fact", "policy", "sop"}
VALID_FACT_STATUSES = {"draft", "approved", "archived"}
VALID_ANSWER_MODES = {"direct_answer", "guided_answer", "handoff_only"}
_BACKEND_OWNED_FIELDS = {
    "parsing_status",
    "parsing_error",
    "parsed_at",
    "indexed_version",
    "indexed_at",
    "chunk_count",
}


def _normalize_key(value: str) -> str:
    return value.strip().lower()


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _suggest_summary(text: str | None) -> str | None:
    normalized = _clean_optional_text(text)
    if not normalized:
        return None
    sentence = re.split(r"(?<=[。！？.!?])\s+", normalized, maxsplit=1)[0].strip()
    summary = sentence or normalized
    if len(summary) > 360:
        summary = f"{summary[:357].rstrip()}..."
    return summary


def _detect_text_language(text: str | None) -> str | None:
    normalized = text or ""
    cjk_count = sum(1 for ch in normalized if "\u4e00" <= ch <= "\u9fff")
    latin_count = sum(1 for ch in normalized.lower() if "a" <= ch <= "z")
    if cjk_count and latin_count:
        return "mixed"
    if cjk_count:
        return "zh"
    if latin_count:
        return "en"
    return None


def _validate_shape(*, status: str, source_type: str, knowledge_kind: str = "document", fact_status: str = "draft", answer_mode: str = "guided_answer") -> None:
    if status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail="Unsupported knowledge status")
    if source_type not in VALID_SOURCE_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported knowledge source_type")
    if knowledge_kind not in VALID_KNOWLEDGE_KINDS:
        raise HTTPException(status_code=400, detail="Unsupported knowledge_kind")
    if fact_status not in VALID_FACT_STATUSES:
        raise HTTPException(status_code=400, detail="Unsupported fact_status")
    if answer_mode not in VALID_ANSWER_MODES:
        raise HTTPException(status_code=400, detail="Unsupported answer_mode")


def _has_draft_content(row: KnowledgeItem) -> bool:
    if bool((row.draft_body or "").strip()) or bool((row.draft_normalized_text or "").strip()):
        return True
    if row.knowledge_kind in {"faq", "business_fact"}:
        return bool((row.fact_question or "").strip() and (row.fact_answer or "").strip())
    return False


def _structured_source_text(row: KnowledgeItem) -> str:
    aliases = [str(item).strip() for item in (row.fact_aliases_json or []) if str(item).strip()]
    parts = [
        f"Question: {(row.fact_question or '').strip()}",
        *(f"Alias: {alias}" for alias in aliases),
        f"Answer: {(row.fact_answer or '').strip()}",
    ]
    return "\n".join(part for part in parts if part.split(":", 1)[-1].strip())


def _published_source_text(row: KnowledgeItem) -> str:
    if row.knowledge_kind in {"faq", "business_fact"} and (row.fact_question or row.fact_answer):
        return _structured_source_text(row)
    return row.draft_body or row.draft_normalized_text or ""


def _snapshot(row: KnowledgeItem, *, version: int, published_at) -> dict:
    return {
        "item_key": row.item_key,
        "title": row.title,
        "summary": row.summary,
        "status": row.status,
        "source_type": row.source_type,
        "knowledge_kind": row.knowledge_kind,
        "market_id": row.market_id,
        "channel": row.channel,
        "audience_scope": row.audience_scope,
        "language": row.language,
        "priority": row.priority,
        "starts_at": row.starts_at.isoformat() if row.starts_at else None,
        "ends_at": row.ends_at.isoformat() if row.ends_at else None,
        "source_url": row.source_url,
        "file_name": row.file_name,
        "file_storage_key": row.file_storage_key,
        "mime_type": row.mime_type,
        "file_size": row.file_size,
        "parsing_status": row.parsing_status,
        "parsing_error": row.parsing_error,
        "fact_question": row.fact_question,
        "fact_answer": row.fact_answer,
        "fact_aliases_json": row.fact_aliases_json or [],
        "fact_status": row.fact_status,
        "answer_mode": row.answer_mode,
        "citation_metadata_json": row.citation_metadata_json or {},
        "body": row.draft_body,
        "normalized_text": row.draft_normalized_text,
        "published_version": version,
        "published_at": published_at.isoformat() if published_at else None,
    }


def list_items(
    db: Session,
    *,
    status: Optional[str] = None,
    source_type: Optional[str] = None,
    market_id: Optional[int] = None,
    channel: Optional[str] = None,
    audience_scope: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[KnowledgeItem], int]:
    query = db.query(KnowledgeItem)
    if status:
        query = query.filter(KnowledgeItem.status == status.strip())
    if source_type:
        query = query.filter(KnowledgeItem.source_type == source_type.strip())
    if market_id is not None:
        query = query.filter(KnowledgeItem.market_id == market_id)
    if channel:
        query = query.filter(KnowledgeItem.channel == channel.strip())
    if audience_scope:
        query = query.filter(KnowledgeItem.audience_scope == audience_scope.strip())
    if q:
        needle = f"%{q.strip()}%"
        query = query.filter(
            or_(
                KnowledgeItem.item_key.ilike(needle),
                KnowledgeItem.title.ilike(needle),
                KnowledgeItem.summary.ilike(needle),
                KnowledgeItem.fact_question.ilike(needle),
                KnowledgeItem.fact_answer.ilike(needle),
                KnowledgeItem.draft_normalized_text.ilike(needle),
                KnowledgeItem.published_normalized_text.ilike(needle),
            )
        )
    total = query.count()
    rows = query.order_by(KnowledgeItem.priority.asc(), KnowledgeItem.item_key.asc()).offset(max(offset, 0)).limit(min(max(limit, 1), 200)).all()
    return rows, total


def get_item_or_404(db: Session, item_id: int) -> KnowledgeItem:
    row = db.query(KnowledgeItem).filter(KnowledgeItem.id == item_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Knowledge item not found")
    return row


def list_versions(db: Session, item_id: int) -> list[KnowledgeItemVersion]:
    return db.query(KnowledgeItemVersion).filter(KnowledgeItemVersion.item_id == item_id).order_by(KnowledgeItemVersion.version.desc()).all()


def create_item(db: Session, payload, actor) -> KnowledgeItem:
    key = _normalize_key(payload.item_key)
    _validate_shape(
        status=payload.status,
        source_type=payload.source_type,
        knowledge_kind=payload.knowledge_kind,
        fact_status=payload.fact_status,
        answer_mode=payload.answer_mode,
    )
    if db.query(KnowledgeItem).filter(KnowledgeItem.item_key == key).first() is not None:
        raise HTTPException(status_code=409, detail="item_key already exists")
    row = KnowledgeItem(
        item_key=key,
        title=payload.title,
        summary=payload.summary,
        status=payload.status,
        source_type=payload.source_type,
        knowledge_kind=payload.knowledge_kind,
        market_id=payload.market_id,
        channel=payload.channel,
        audience_scope=payload.audience_scope,
        language=payload.language,
        priority=payload.priority,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        source_url=payload.source_url,
        file_name=payload.file_name,
        file_storage_key=payload.file_storage_key,
        mime_type=payload.mime_type,
        file_size=payload.file_size,
        parsing_status="unparsed",
        parsing_error=None,
        fact_question=payload.fact_question,
        fact_answer=payload.fact_answer,
        fact_aliases_json=payload.fact_aliases_json or [],
        fact_status=payload.fact_status,
        answer_mode=payload.answer_mode,
        citation_metadata_json=payload.citation_metadata_json or {},
        draft_body=payload.draft_body,
        draft_normalized_text=payload.draft_normalized_text or payload.draft_body or None,
        created_by=getattr(actor, "id", None),
        updated_by=getattr(actor, "id", None),
    )
    db.add(row)
    db.flush()
    return row


def create_file_item_from_upload(
    db: Session,
    *,
    file: UploadFile,
    actor,
    item_key: str | None = None,
    title: str | None = None,
    market_id: int | None = None,
    channel: str | None = "website",
    audience_scope: str | None = "customer",
    language: str | None = None,
) -> KnowledgeItem:
    filename = file.filename or "knowledge.txt"
    key = _normalize_key(_clean_optional_text(item_key) or _safe_item_key_from_filename(filename))
    if db.query(KnowledgeItem).filter(KnowledgeItem.item_key == key).first() is not None:
        raise HTTPException(status_code=409, detail="item_key already exists")
    row = KnowledgeItem(
        item_key=key,
        title=_clean_optional_text(title) or filename,
        summary=None,
        status="draft",
        source_type="file",
        knowledge_kind="document",
        market_id=market_id,
        channel=_clean_optional_text(channel),
        audience_scope=_clean_optional_text(audience_scope) or "customer",
        language=_clean_optional_text(language),
        priority=100,
        parsing_status="unparsed",
        parsing_error=None,
        created_by=getattr(actor, "id", None),
        updated_by=getattr(actor, "id", None),
    )
    db.add(row)
    db.flush()
    return upload_document(db, row, file, actor)


def upload_document(db: Session, row: KnowledgeItem, file: UploadFile, actor) -> KnowledgeItem:
    content = read_upload_bytes(file)
    parsed_body, normalized_text = parse_document_bytes(
        content=content,
        filename=file.filename,
        mime_type=file.content_type,
    )
    stored = file_service.save_upload(file)
    now = utc_now()
    row.source_type = "file"
    row.file_name = stored.stored_name
    row.file_storage_key = stored.storage_key
    row.mime_type = stored.mime_type
    row.file_size = stored.file_size
    row.draft_body = parsed_body
    row.draft_normalized_text = normalized_text
    if not row.summary:
        row.summary = _suggest_summary(normalized_text)
    if not row.language:
        row.language = _detect_text_language(normalized_text)
    row.parsing_status = "parsed"
    row.parsing_error = None
    row.parsed_at = now
    row.updated_by = getattr(actor, "id", None)
    db.flush()
    return row


def update_item(db: Session, row: KnowledgeItem, payload, actor) -> KnowledgeItem:
    values = payload.model_dump(exclude_unset=True)
    for field in _BACKEND_OWNED_FIELDS:
        values.pop(field, None)
    status = values.get("status", row.status)
    source_type = values.get("source_type", row.source_type)
    knowledge_kind = values.get("knowledge_kind", row.knowledge_kind)
    fact_status = values.get("fact_status", row.fact_status)
    answer_mode = values.get("answer_mode", row.answer_mode)
    _validate_shape(status=status, source_type=source_type, knowledge_kind=knowledge_kind, fact_status=fact_status, answer_mode=answer_mode)
    if values.get("draft_normalized_text") is None and "draft_body" in values:
        values["draft_normalized_text"] = values.get("draft_body")
    if "fact_aliases_json" in values and values["fact_aliases_json"] is None:
        values["fact_aliases_json"] = []
    if "citation_metadata_json" in values and values["citation_metadata_json"] is None:
        values["citation_metadata_json"] = {}
    for key, value in values.items():
        setattr(row, key, value)
    row.updated_by = getattr(actor, "id", None)
    db.flush()
    return row


def publish_item(db: Session, row: KnowledgeItem, actor, *, notes: Optional[str] = None) -> KnowledgeItemVersion:
    if not _has_draft_content(row):
        raise HTTPException(status_code=400, detail="Draft knowledge content is empty")
    new_version = (row.published_version or 0) + 1
    published_at = utc_now()
    version_row = KnowledgeItemVersion(
        item_id=row.id,
        version=new_version,
        snapshot_json=_snapshot(row, version=new_version, published_at=published_at),
        summary=row.summary,
        notes=notes,
        published_by=getattr(actor, "id", None),
        published_at=published_at,
    )
    source_text = _published_source_text(row)
    row.published_body = row.draft_body or source_text
    row.published_normalized_text = row.draft_normalized_text or source_text
    row.published_version = new_version
    row.published_at = published_at
    row.published_by = getattr(actor, "id", None)
    row.updated_by = getattr(actor, "id", None)
    if row.status == "draft":
        row.status = "active"
    db.add(version_row)
    index_published_item(db, row)
    db.flush()
    return version_row


def rollback_item(db: Session, row: KnowledgeItem, *, version: int, actor, notes: Optional[str] = None) -> KnowledgeItemVersion:
    target = db.query(KnowledgeItemVersion).filter(
        KnowledgeItemVersion.item_id == row.id,
        KnowledgeItemVersion.version == version,
    ).first()
    if target is None:
        raise HTTPException(status_code=404, detail="Knowledge item version not found")
    snapshot = target.snapshot_json or {}
    row.title = snapshot.get("title") or row.title
    row.summary = snapshot.get("summary")
    row.status = snapshot.get("status") or row.status
    row.source_type = snapshot.get("source_type") or row.source_type
    row.knowledge_kind = snapshot.get("knowledge_kind") or row.knowledge_kind
    row.market_id = snapshot.get("market_id")
    row.channel = snapshot.get("channel")
    row.audience_scope = snapshot.get("audience_scope") or row.audience_scope
    row.language = snapshot.get("language")
    row.priority = snapshot.get("priority") or row.priority
    row.source_url = snapshot.get("source_url")
    row.file_name = snapshot.get("file_name")
    row.file_storage_key = snapshot.get("file_storage_key")
    row.mime_type = snapshot.get("mime_type")
    row.file_size = snapshot.get("file_size")
    row.parsing_status = snapshot.get("parsing_status") or row.parsing_status
    row.parsing_error = snapshot.get("parsing_error")
    row.fact_question = snapshot.get("fact_question")
    row.fact_answer = snapshot.get("fact_answer")
    row.fact_aliases_json = snapshot.get("fact_aliases_json") or []
    row.fact_status = snapshot.get("fact_status") or row.fact_status
    row.answer_mode = snapshot.get("answer_mode") or row.answer_mode
    row.citation_metadata_json = snapshot.get("citation_metadata_json") or {}
    row.draft_body = snapshot.get("body")
    row.draft_normalized_text = snapshot.get("normalized_text")
    return publish_item(db, row, actor, notes=notes or f"Rollback to v{version}")


def search_published(
    db: Session,
    *,
    q: Optional[str] = None,
    market_id: Optional[int] = None,
    channel: Optional[str] = None,
    audience_scope: Optional[str] = "customer",
    limit: int = 20,
) -> tuple[list[KnowledgeItem], int]:
    now = utc_now()
    query = db.query(KnowledgeItem).filter(
        KnowledgeItem.status == "active",
        KnowledgeItem.published_version > 0,
        or_(KnowledgeItem.starts_at.is_(None), KnowledgeItem.starts_at <= now),
        or_(KnowledgeItem.ends_at.is_(None), KnowledgeItem.ends_at >= now),
    )
    if market_id is not None:
        query = query.filter(or_(KnowledgeItem.market_id.is_(None), KnowledgeItem.market_id == market_id))
    if channel:
        query = query.filter(or_(KnowledgeItem.channel.is_(None), KnowledgeItem.channel == channel.strip()))
    if audience_scope:
        query = query.filter(KnowledgeItem.audience_scope == audience_scope.strip())
    if q:
        needle = f"%{q.strip()}%"
        query = query.filter(
            or_(
                KnowledgeItem.item_key.ilike(needle),
                KnowledgeItem.title.ilike(needle),
                KnowledgeItem.summary.ilike(needle),
                KnowledgeItem.fact_question.ilike(needle),
                KnowledgeItem.fact_answer.ilike(needle),
                KnowledgeItem.published_normalized_text.ilike(needle),
            )
        )
    total = query.count()
    rows = query.order_by(KnowledgeItem.priority.asc(), KnowledgeItem.item_key.asc()).limit(min(max(limit, 1), 100)).all()
    return rows, total


def evaluate_golden_test(payload, retrieval) -> tuple[bool, list[dict]]:
    hits = list(getattr(retrieval, "hits", []) or [])
    top_hit = hits[0] if hits else None
    top_score = float(getattr(top_hit, "score", 0.0) or 0.0)
    min_score = float(getattr(payload, "min_score", 0.0) or 0.0)
    expected_item_key = _clean_optional_text(getattr(payload, "expected_item_key", None))
    expected_answer_contains = _clean_optional_text(getattr(payload, "expected_answer_contains", None))
    forbidden_terms = [
        term
        for term in (_clean_optional_text(str(item)) for item in (getattr(payload, "forbidden_answer_terms", []) or []))
        if term
    ][:20]

    hit_keys = [str(getattr(hit, "item_key", "") or "").lower() for hit in hits]
    answer_texts = []
    for hit in hits:
        direct_answer = _clean_optional_text(getattr(hit, "direct_answer", None))
        chunk_text = _clean_optional_text(getattr(hit, "text", None))
        if direct_answer:
            answer_texts.append(direct_answer)
        if chunk_text:
            answer_texts.append(chunk_text)
    combined_answer_text = "\n".join(answer_texts)
    normalized_answers = _normalize_assertion_text(combined_answer_text)

    assertions: list[dict] = []
    assertions.append(
        {
            "key": "top-hit-score",
            "label": "Top hit score",
            "passed": bool(top_hit and top_score >= min_score),
            "expected": f">= {min_score:g}",
            "actual": f"{top_score:g}" if top_hit else "no published hit",
            "evidence": getattr(top_hit, "item_key", None) or "retrieve-test returned no hit",
        }
    )

    if expected_item_key:
        normalized_expected_key = expected_item_key.lower()
        assertions.append(
            {
                "key": "expected-source",
                "label": "Expected source item",
                "passed": normalized_expected_key in hit_keys,
                "expected": normalized_expected_key,
                "actual": ", ".join(hit_keys[:5]) or "no published hit",
                "evidence": "retrieval hits include expected item_key" if normalized_expected_key in hit_keys else "expected item_key missing from hits",
            }
        )

    if expected_answer_contains:
        normalized_expected_answer = _normalize_assertion_text(expected_answer_contains)
        answer_matched = bool(normalized_expected_answer and normalized_expected_answer in normalized_answers)
        assertions.append(
            {
                "key": "expected-answer",
                "label": "Expected answer evidence",
                "passed": answer_matched,
                "expected": expected_answer_contains,
                "actual": combined_answer_text[:300] if combined_answer_text else "no answer text",
                "evidence": "expected answer text was present in retrieved evidence" if answer_matched else "expected answer text was not present",
            }
        )

    found_forbidden = [
        term
        for term in forbidden_terms
        if _normalize_assertion_text(term) and _normalize_assertion_text(term) in normalized_answers
    ]
    assertions.append(
        {
            "key": "forbidden-answer",
            "label": "Forbidden answer guard",
            "passed": not found_forbidden,
            "expected": "no forbidden terms" if forbidden_terms else "no forbidden terms configured",
            "actual": ", ".join(found_forbidden) if found_forbidden else "none",
            "evidence": "retrieved evidence does not contain forbidden terms" if not found_forbidden else "forbidden term appeared in retrieved evidence",
        }
    )

    return all(item["passed"] for item in assertions), assertions


def _normalize_assertion_text(value: str | None) -> str:
    return " ".join((value or "").strip().lower().split())


def _safe_item_key_from_filename(filename: str) -> str:
    import re

    stem = filename.rsplit(".", 1)[0].strip().lower() or "knowledge"
    cleaned = re.sub(r"[^a-z0-9_.-]+", "-", stem).strip("-_.") or "knowledge"
    return f"kb.{cleaned}"[:120]
