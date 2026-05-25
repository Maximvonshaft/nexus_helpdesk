from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models_control_plane import KnowledgeChunk, KnowledgeItem
from ..utils.time import utc_now
from .knowledge_document_service import normalize_document_text

MAX_CHUNK_CHARS = 900
CHUNK_OVERLAP_CHARS = 120
MAX_QUERY_TERMS = 8


@dataclass(frozen=True)
class KnowledgeChunkHit:
    item_id: int
    item_key: str
    title: str
    published_version: int
    chunk_index: int
    score: float
    text: str
    metadata: dict[str, Any]


def chunk_document_text(text: str, *, max_chars: int = MAX_CHUNK_CHARS, overlap_chars: int = CHUNK_OVERLAP_CHARS) -> list[str]:
    normalized = normalize_document_text(text)
    if not normalized:
        return []
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", text) if part.strip()]
    if len(paragraphs) <= 1:
        paragraphs = [normalized]

    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        cleaned = normalize_document_text(paragraph)
        if not cleaned:
            continue
        if len(cleaned) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_long_text(cleaned, max_chars=max_chars, overlap_chars=overlap_chars))
            continue
        candidate = f"{current}\n\n{cleaned}".strip() if current else cleaned
        if len(candidate) <= max_chars:
            current = candidate
        else:
            chunks.append(current)
            overlap = current[-overlap_chars:].strip() if overlap_chars > 0 else ""
            current = f"{overlap}\n\n{cleaned}".strip() if overlap else cleaned
    if current:
        chunks.append(current)
    return [item for item in chunks if normalize_document_text(item)]


def index_published_item(db: Session, item: KnowledgeItem) -> int:
    published_version = int(item.published_version or 0)
    if published_version <= 0:
        item.indexed_version = 0
        item.indexed_at = None
        item.chunk_count = 0
        return 0

    source_text = item.published_normalized_text or item.published_body or ""
    chunks = chunk_document_text(source_text)
    db.query(KnowledgeChunk).filter(
        KnowledgeChunk.item_id == item.id,
        KnowledgeChunk.published_version == published_version,
    ).delete(synchronize_session=False)

    for index, chunk_text in enumerate(chunks):
        normalized = normalize_document_text(chunk_text)
        db.add(
            KnowledgeChunk(
                item_id=item.id,
                item_key=item.item_key,
                title=item.title,
                published_version=published_version,
                chunk_index=index,
                chunk_text=chunk_text,
                normalized_text=normalized,
                content_hash=hashlib.sha256(normalized.encode("utf-8", errors="ignore")).hexdigest(),
                market_id=item.market_id,
                channel=item.channel,
                audience_scope=item.audience_scope,
                starts_at=item.starts_at,
                ends_at=item.ends_at,
                status=item.status,
                priority=item.priority,
                source_type=item.source_type,
                file_name=item.file_name,
                metadata_json={
                    "source_type": item.source_type,
                    "file_name": item.file_name,
                    "audience_scope": item.audience_scope,
                    "channel": item.channel,
                    "market_id": item.market_id,
                    "priority": item.priority,
                    "published_at": item.published_at.isoformat() if item.published_at else None,
                },
            )
        )

    item.indexed_version = published_version
    item.indexed_at = utc_now()
    item.chunk_count = len(chunks)
    db.flush()
    return len(chunks)


def search_published_chunks(
    db: Session,
    *,
    q: str | None,
    market_id: int | None = None,
    channel: str | None = None,
    audience_scope: str | None = "customer",
    limit: int = 5,
) -> tuple[list[KnowledgeChunkHit], int]:
    now = utc_now()
    query = (
        db.query(KnowledgeChunk)
        .join(KnowledgeItem, KnowledgeItem.id == KnowledgeChunk.item_id)
        .filter(
            KnowledgeChunk.status == "active",
            KnowledgeChunk.published_version > 0,
            KnowledgeChunk.published_version == KnowledgeItem.published_version,
            or_(KnowledgeChunk.starts_at.is_(None), KnowledgeChunk.starts_at <= now),
            or_(KnowledgeChunk.ends_at.is_(None), KnowledgeChunk.ends_at >= now),
        )
    )
    if market_id is not None:
        query = query.filter(or_(KnowledgeChunk.market_id.is_(None), KnowledgeChunk.market_id == market_id))
    if channel:
        query = query.filter(or_(KnowledgeChunk.channel.is_(None), KnowledgeChunk.channel == channel.strip()))
    if audience_scope:
        query = query.filter(KnowledgeChunk.audience_scope == audience_scope.strip())

    terms = _query_terms(q)
    if terms:
        predicates = []
        for term in terms:
            needle = f"%{term}%"
            predicates.extend(
                [
                    KnowledgeChunk.normalized_text.ilike(needle),
                    KnowledgeChunk.title.ilike(needle),
                ]
            )
        query = query.filter(or_(*predicates))

    candidates = query.order_by(KnowledgeChunk.priority.asc(), KnowledgeChunk.chunk_index.asc()).limit(max(limit * 8, 40)).all()
    hits = [_hit_from_row(chunk, terms=terms, q=q, market_id=market_id, channel=channel) for chunk in candidates]
    if terms:
        hits = [hit for hit in hits if hit.score > 0]
    hits.sort(key=lambda hit: (-hit.score, hit.metadata.get("priority", 10000), hit.item_key, hit.chunk_index))
    return hits[: max(1, min(limit, 20))], len(hits)


def _split_long_text(text: str, *, max_chars: int, overlap_chars: int) -> list[str]:
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        next_start = max(start + 1, end - max(0, overlap_chars))
        start = next_start
    return chunks


def _query_terms(value: str | None) -> list[str]:
    lowered = (value or "").lower()
    raw_terms = re.findall(r"[a-z0-9]{2,}|[\u4e00-\u9fff]{2,}", lowered)
    seen: set[str] = set()
    terms: list[str] = []
    for term in raw_terms:
        if term in seen:
            continue
        seen.add(term)
        terms.append(term)
        if len(terms) >= MAX_QUERY_TERMS:
            break
    return terms


def _hit_from_row(
    chunk: KnowledgeChunk,
    *,
    terms: Iterable[str],
    q: str | None,
    market_id: int | None,
    channel: str | None,
) -> KnowledgeChunkHit:
    term_list = list(terms)
    normalized_text = (chunk.normalized_text or "").lower()
    title = (chunk.title or "").lower()
    query_phrase = normalize_document_text(q).lower()
    score = 0.0
    if query_phrase and query_phrase in normalized_text:
        score += 6.0
    for term in term_list:
        if term in title:
            score += 4.0
        if term in normalized_text:
            score += 1.0 + min(normalized_text.count(term), 3) * 0.35
    if not term_list:
        score += max(0.0, 10.0 - min(chunk.priority or 100, 1000) / 100.0)
    if market_id is not None and chunk.market_id == market_id:
        score += 0.75
    if channel and chunk.channel == channel:
        score += 0.75

    metadata = dict(chunk.metadata_json or {})
    metadata.update(
        {
            "source_type": chunk.source_type,
            "file_name": chunk.file_name,
            "market_id": chunk.market_id,
            "channel": chunk.channel,
            "audience_scope": chunk.audience_scope,
            "priority": chunk.priority,
        }
    )
    return KnowledgeChunkHit(
        item_id=chunk.item_id,
        item_key=chunk.item_key,
        title=chunk.title,
        published_version=chunk.published_version,
        chunk_index=chunk.chunk_index,
        score=round(score, 3),
        text=chunk.chunk_text,
        metadata=metadata,
    )
