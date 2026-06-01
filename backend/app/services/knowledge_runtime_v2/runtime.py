from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Text, or_
from sqlalchemy.orm import Session

from ...models_control_plane import KnowledgeChunk, KnowledgeItem
from ...settings import get_settings
from ...utils.time import utc_now
from .embeddings import cosine_similarity, get_embedding_provider

STRUCTURED_KINDS = {"faq", "business_fact"}
TRACKING_TERMS = {"tracking", "track", "waybill", "物流", "运单", "单号", "包裹", "查件"}


@dataclass(frozen=True)
class KnowledgeRuntimeOptions:
    include_degraded: bool = True
    allow_legacy_candidate_source: bool = True


@dataclass(frozen=True)
class KnowledgeRuntimeHit:
    item_id: int
    item_key: str
    title: str
    published_version: int
    chunk_index: int
    score: float
    text: str
    metadata: dict[str, Any]
    retrieval_method: str
    matched_terms: list[str] = field(default_factory=list)
    score_breakdown: dict[str, float] = field(default_factory=dict)
    direct_answer: str | None = None
    answer_mode: str | None = None
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class KnowledgeRuntimeResult:
    hits: list[KnowledgeRuntimeHit]
    direct_facts: list[dict[str, Any]]
    locked_facts: list[dict[str, Any]]
    confidence: float
    no_answer_reason: str | None
    trace: dict[str, Any]
    retrieval_methods: list[str]
    latency_ms: int

    @property
    def total(self) -> int:
        return len(self.hits)

    def as_trace(self) -> dict[str, Any]:
        return self.trace


def retrieve_knowledge(
    db: Session,
    *,
    query: str,
    tenant_key: str | None = None,
    market_id: int | None = None,
    channel: str | None = None,
    audience_scope: str = "customer",
    language: str | None = None,
    limit: int = 8,
    options: KnowledgeRuntimeOptions | None = None,
) -> KnowledgeRuntimeResult:
    started = time.monotonic()
    settings = get_settings()
    options = options or KnowledgeRuntimeOptions()
    normalized = _normalize(query)
    terms = _terms(normalized)
    filters = {
        "tenant_key": tenant_key,
        "market_id": market_id,
        "channel": channel,
        "audience_scope": audience_scope,
        "language": language,
        "probe_exclusion": True,
    }
    candidate_rows = _candidate_rows(db, terms=terms, normalized_query=normalized, market_id=market_id, channel=channel, audience_scope=audience_scope, language=language)
    candidate_hits: dict[tuple[int, int, int], KnowledgeRuntimeHit] = {}
    source_counts = {"structured_exact": 0, "fts": 0, "vector": 0, "legacy_candidate": len(candidate_rows)}
    vector_degraded: str | None = None

    for chunk, item in candidate_rows:
        hit = _score_row(chunk, item, terms=terms, normalized_query=normalized, retrieval_source="fts")
        if hit.score > 0 or not terms:
            candidate_hits[(hit.item_id, hit.published_version, hit.chunk_index)] = hit
            if "structured_exact" in hit.retrieval_method:
                source_counts["structured_exact"] += 1
            if "fts" in hit.retrieval_method:
                source_counts["fts"] += 1

    if settings.knowledge_embeddings_enabled:
        try:
            provider = get_embedding_provider(settings.knowledge_embedding_provider, dim=settings.knowledge_embedding_dim)
            query_embedding = provider.embed_texts([normalized])[0]
            vector_rows = [
                (chunk, item, cosine_similarity(query_embedding, chunk.embedding))
                for chunk, item in candidate_rows
                if isinstance(chunk.embedding, list) and chunk.embedding
            ]
            vector_rows.sort(key=lambda item: item[2], reverse=True)
            for chunk, item, similarity in vector_rows[: max(limit * 4, 20)]:
                if similarity <= 0:
                    continue
                hit = _score_row(chunk, item, terms=terms, normalized_query=normalized, retrieval_source="vector", vector_score=similarity)
                key = (hit.item_id, hit.published_version, hit.chunk_index)
                previous = candidate_hits.get(key)
                candidate_hits[key] = _merge_hits(previous, hit) if previous else hit
                source_counts["vector"] += 1
        except Exception as exc:
            vector_degraded = type(exc).__name__
    else:
        vector_degraded = "disabled"

    fused = _rrf_fuse(list(candidate_hits.values()))
    fused.sort(key=lambda hit: (-hit.score, hit.metadata.get("priority") or 10000, hit.item_key, hit.chunk_index))
    hits = fused[: max(1, min(limit, 20))]
    direct_facts = [_fact_from_hit(hit) for hit in hits if hit.direct_answer and hit.metadata.get("fact_status") == "approved"]
    direct_facts = [fact for fact in direct_facts if fact]
    locked_facts = direct_facts[:3]
    confidence = round(min(1.0, (hits[0].score / 100.0) if hits else 0.0), 3)
    no_answer_reason = None if hits else "no_evidence"
    methods = sorted({method for hit in hits for method in hit.retrieval_method.split("+") if method})
    if vector_degraded:
        methods.append("vector_degraded")
    latency_ms = int((time.monotonic() - started) * 1000)
    trace = {
        "retrieval": "hybrid_rag_v2",
        "filters": filters,
        "query": {"normalized": normalized, "terms": terms},
        "candidates_by_source": source_counts,
        "fusion": "reciprocal_rank_fusion",
        "rerank": {"strategy": "deterministic_policy_v1", "top_item_keys": [hit.item_key for hit in hits[:5]]},
        "evidence_selected": [_trace_hit(hit) for hit in hits[:8]],
        "retrieval_methods": methods,
        "vector": {
            "enabled": settings.knowledge_embeddings_enabled,
            "provider": settings.knowledge_embedding_provider,
            "model": settings.knowledge_embedding_model,
            "dim": settings.knowledge_embedding_dim,
            "degraded_reason": vector_degraded,
            "fallback_allowed": settings.knowledge_vector_fallback_allowed,
        },
        "no_answer_reason": no_answer_reason,
        "latency_ms": latency_ms,
    }
    return KnowledgeRuntimeResult(
        hits=hits,
        direct_facts=direct_facts,
        locked_facts=locked_facts,
        confidence=confidence,
        no_answer_reason=no_answer_reason,
        trace=trace,
        retrieval_methods=methods,
        latency_ms=latency_ms,
    )


def _candidate_rows(
    db: Session,
    *,
    terms: list[str],
    normalized_query: str,
    market_id: int | None,
    channel: str | None,
    audience_scope: str,
    language: str | None,
) -> list[tuple[KnowledgeChunk, KnowledgeItem]]:
    now = utc_now()
    query = (
        db.query(KnowledgeChunk, KnowledgeItem)
        .join(KnowledgeItem, KnowledgeItem.id == KnowledgeChunk.item_id)
        .filter(
            KnowledgeChunk.status == "active",
            KnowledgeItem.status == "active",
            KnowledgeChunk.published_version > 0,
            KnowledgeChunk.published_version == KnowledgeItem.published_version,
            or_(KnowledgeChunk.starts_at.is_(None), KnowledgeChunk.starts_at <= now),
            or_(KnowledgeChunk.ends_at.is_(None), KnowledgeChunk.ends_at >= now),
            or_(KnowledgeItem.knowledge_kind.is_(None), KnowledgeItem.knowledge_kind.notin_(tuple(STRUCTURED_KINDS)), KnowledgeItem.fact_status == "approved"),
        )
    )
    query = _exclude_probe_rows(query)
    if market_id is not None:
        query = query.filter(or_(KnowledgeChunk.market_id.is_(None), KnowledgeChunk.market_id == market_id))
    if channel:
        query = query.filter(or_(KnowledgeChunk.channel.is_(None), KnowledgeChunk.channel == channel.strip()))
    if audience_scope:
        query = query.filter(KnowledgeChunk.audience_scope == audience_scope.strip())
    if language:
        lang = language.strip().lower()
        query = query.filter(or_(KnowledgeChunk.language.is_(None), KnowledgeChunk.language == lang, KnowledgeChunk.language.like(f"{lang}-%")))
    needles = [normalized_query, *terms][:25]
    if needles:
        predicates = []
        for term in needles:
            if not term:
                continue
            needle = f"%{term}%"
            predicates.extend([
                KnowledgeChunk.normalized_text.ilike(needle),
                KnowledgeChunk.title.ilike(needle),
                KnowledgeItem.item_key.ilike(needle),
                KnowledgeItem.title.ilike(needle),
                KnowledgeItem.summary.ilike(needle),
                KnowledgeItem.fact_question.ilike(needle),
                KnowledgeItem.fact_answer.ilike(needle),
            ])
        query = query.filter(or_(*predicates))
    return query.order_by(KnowledgeItem.priority.asc(), KnowledgeChunk.priority.asc(), KnowledgeChunk.chunk_index.asc()).limit(320).all()


def _exclude_probe_rows(query):
    probe_title = "[PROBE]%"
    return query.filter(
        KnowledgeItem.item_key.notilike("%probe%"),
        KnowledgeChunk.item_key.notilike("%probe%"),
        KnowledgeItem.title.notlike(probe_title),
        KnowledgeChunk.title.notlike(probe_title),
        or_(KnowledgeItem.citation_metadata_json.is_(None), ~KnowledgeItem.citation_metadata_json.cast(Text).ilike("%probe_category%")),
        or_(KnowledgeChunk.metadata_json.is_(None), ~KnowledgeChunk.metadata_json.cast(Text).ilike("%probe_category%")),
        or_(KnowledgeChunk.metadata_json.is_(None), ~KnowledgeChunk.metadata_json.cast(Text).ilike("%probe_seed%")),
    )


def _score_row(chunk: KnowledgeChunk, item: KnowledgeItem, *, terms: list[str], normalized_query: str, retrieval_source: str, vector_score: float = 0.0) -> KnowledgeRuntimeHit:
    text_value = _normalize(" ".join([chunk.title or "", chunk.normalized_text or chunk.chunk_text or "", item.fact_question or "", item.fact_answer or "", item.item_key or ""]))
    matched = [term for term in terms if term in text_value]
    structured = (item.knowledge_kind or "document") in STRUCTURED_KINDS and item.fact_status == "approved"
    direct = structured and item.answer_mode == "direct_answer" and bool((item.fact_answer or "").strip())
    breakdown: dict[str, float] = {}
    methods: set[str] = set()
    if structured:
        breakdown["structured_exact"] = 18.0
        methods.add("structured_exact")
    if normalized_query and normalized_query in text_value:
        breakdown["exact_phrase"] = 20.0
        methods.add("structured_exact" if structured else "fts")
    if matched:
        breakdown["fts"] = min(42.0, len(matched) * 5.0)
        methods.add("fts")
    if direct:
        breakdown["direct_answer"] = 14.0
        methods.add("structured_exact")
    if vector_score > 0:
        breakdown["vector"] = round(vector_score * 24.0, 3)
        methods.add("vector")
    priority = int(chunk.priority or item.priority or 100)
    breakdown["priority"] = max(0.0, 6.0 - min(priority, 600) / 100.0)
    score = round(sum(breakdown.values()), 3)
    metadata = dict(chunk.metadata_json or {})
    metadata.update({
        "priority": priority,
        "knowledge_kind": item.knowledge_kind,
        "fact_status": item.fact_status,
        "answer_mode": item.answer_mode,
        "citation": item.citation_metadata_json or metadata.get("citation") or {},
        "retrieval_method": "+".join(sorted(methods or {retrieval_source})),
        "matched_terms": matched[:16],
        "score_breakdown": breakdown,
    })
    return KnowledgeRuntimeHit(
        item_id=chunk.item_id,
        item_key=chunk.item_key,
        title=chunk.title,
        published_version=chunk.published_version,
        chunk_index=chunk.chunk_index,
        score=score,
        text=chunk.chunk_text,
        metadata=metadata,
        retrieval_method="+".join(sorted(methods or {retrieval_source})),
        matched_terms=matched[:16],
        score_breakdown=breakdown,
        direct_answer=(item.fact_answer or "").strip() if direct else None,
        answer_mode=item.answer_mode,
        source_metadata={
            "item_key": item.item_key,
            "title": item.title,
            "published_version": item.published_version,
            "chunk_index": chunk.chunk_index,
            "citation": item.citation_metadata_json or metadata.get("citation") or {},
        },
    )


def _merge_hits(left: KnowledgeRuntimeHit | None, right: KnowledgeRuntimeHit) -> KnowledgeRuntimeHit:
    if left is None:
        return right
    score = round(left.score + right.score, 3)
    methods = "+".join(sorted(set(left.retrieval_method.split("+")) | set(right.retrieval_method.split("+"))))
    breakdown = {**left.score_breakdown}
    for key, value in right.score_breakdown.items():
        breakdown[key] = breakdown.get(key, 0.0) + value
    return KnowledgeRuntimeHit(**{**left.__dict__, "score": score, "retrieval_method": methods, "score_breakdown": breakdown, "matched_terms": sorted(set(left.matched_terms + right.matched_terms))})


def _rrf_fuse(hits: list[KnowledgeRuntimeHit]) -> list[KnowledgeRuntimeHit]:
    ranked = sorted(hits, key=lambda hit: hit.score, reverse=True)
    fused: list[KnowledgeRuntimeHit] = []
    for rank, hit in enumerate(ranked, start=1):
        fused_score = round(hit.score + (60.0 / (60 + rank)), 3)
        fused.append(KnowledgeRuntimeHit(**{**hit.__dict__, "score": fused_score}))
    return fused


def _fact_from_hit(hit: KnowledgeRuntimeHit) -> dict[str, Any]:
    return {
        "item_key": hit.item_key,
        "title": hit.title,
        "answer": hit.direct_answer,
        "answer_mode": hit.answer_mode,
        "source": hit.source_metadata,
        "score": hit.score,
    }


def _trace_hit(hit: KnowledgeRuntimeHit) -> dict[str, Any]:
    return {
        "item_key": hit.item_key,
        "title": hit.title,
        "score": hit.score,
        "chunk_index": hit.chunk_index,
        "retrieval_method": hit.retrieval_method,
        "matched_terms": hit.matched_terms,
        "answer_mode": hit.answer_mode,
        "source_metadata": hit.source_metadata,
    }


def _normalize(value: str | None) -> str:
    return " ".join(str(value or "").lower().strip().split())


def _terms(value: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9][a-z0-9_-]{1,}|[\u4e00-\u9fff]{2,}", value, flags=re.I)
    cjk = []
    for phrase in re.findall(r"[\u4e00-\u9fff]{3,}", value):
        cjk.extend(phrase[index:index + 2] for index in range(max(0, len(phrase) - 1)))
    items: list[str] = []
    seen: set[str] = set()
    for token in [*tokens, *cjk, *TRACKING_TERMS]:
        cleaned = token.strip().lower()
        if cleaned and cleaned in value and cleaned not in seen:
            seen.add(cleaned)
            items.append(cleaned)
    return items[:32]
