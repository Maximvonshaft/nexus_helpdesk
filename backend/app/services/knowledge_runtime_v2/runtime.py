from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Text, text, or_
from sqlalchemy.orm import Session

from ...models_control_plane import KnowledgeChunk, KnowledgeItem
from ...settings import get_settings
from ...utils.time import utc_now
from .embeddings import cosine_similarity, get_embedding_provider, vector_literal

STRUCTURED_KINDS = {"faq", "business_fact"}
TRACKING_TERMS = {"tracking", "track", "waybill", "物流", "运单", "单号", "包裹", "查件"}
GLOBAL_COUNTRY_SCOPE = "GLOBAL"
GLOBAL_CHANNEL_SCOPE = "all"
CUSTOMER_VISIBILITY = "customer"
CUSTOMER_SHAREABILITY = {"customer_visible", "runtime_context"}
AUTHORITY_RANK = {"tool": 0, "official_policy": 1, "policy": 2, "sop": 3, "faq": 4, "imported": 5}
HIGH_RISK_TERMS = {
    "refund", "赔付", "赔偿", "退款", "claim", "customs", "清关", "tax", "duty", "delivery time", "时效", "物流状态", "tracking status"
}
MIN_HIGH_RISK_SCORE = 18.0


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
    brand_id: str | None = None,
    country_scope: str | None = None,
    channel_scope: str | None = None,
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
    tenant_id = _scope_value(tenant_key, "default")
    brand = _scope_value(brand_id, "default")
    country = _country_scope(country_scope)
    channel_filter = _scope_value(channel_scope or channel, GLOBAL_CHANNEL_SCOPE)
    filters = {
        "tenant_id": tenant_id,
        "brand_id": brand,
        "country_scope": country,
        "country_fallback": GLOBAL_COUNTRY_SCOPE,
        "channel_scope": channel_filter,
        "channel_fallback": GLOBAL_CHANNEL_SCOPE,
        "market_id": market_id,
        "channel": channel,
        "audience_scope": audience_scope,
        "language": language,
        "visibility": CUSTOMER_VISIBILITY,
        "shareability": sorted(CUSTOMER_SHAREABILITY),
        "probe_exclusion": True,
    }
    candidate_rows = _candidate_rows(db, terms=terms, normalized_query=normalized, tenant_id=tenant_id, brand_id=brand, country_scope=country, channel_scope=channel_filter, market_id=market_id, channel=channel, audience_scope=audience_scope, language=language)
    candidate_hits: dict[tuple[int, int, int], KnowledgeRuntimeHit] = {}
    source_counts = {"structured_exact": 0, "fts": 0, "postgres_fts": 0, "vector": 0, "pgvector": 0, "legacy_candidate": len(candidate_rows)}
    vector_degraded: str | None = None

    for chunk, item in candidate_rows:
        source = "postgres_fts" if _is_postgres(db) else "fts"
        hit = _score_row(chunk, item, terms=terms, normalized_query=normalized, retrieval_source=source)
        if hit.score > 0 or not terms:
            candidate_hits[(hit.item_id, hit.published_version, hit.chunk_index)] = hit
            if "structured_exact" in hit.retrieval_method:
                source_counts["structured_exact"] += 1
            if "fts" in hit.retrieval_method:
                source_counts["fts"] += 1
            if "postgres_fts" in hit.retrieval_method:
                source_counts["postgres_fts"] += 1

    if settings.knowledge_embeddings_enabled:
        try:
            provider = get_embedding_provider(
                settings.knowledge_embedding_provider,
                dim=settings.knowledge_embedding_dim,
                model=settings.knowledge_embedding_model,
                base_url=settings.knowledge_embedding_base_url,
                api_key=settings.knowledge_embedding_api_key,
                api_key_file=settings.knowledge_embedding_api_key_file,
                timeout_seconds=settings.knowledge_embedding_timeout_seconds,
            )
            query_embedding = provider.embed_texts([normalized])[0]
            vector_rows = _vector_rows(db, query_embedding=query_embedding, fallback_rows=candidate_rows, tenant_id=tenant_id, brand_id=brand, country_scope=country, channel_scope=channel_filter, market_id=market_id, channel=channel, audience_scope=audience_scope, language=language, limit=limit)
            vector_rows.sort(key=lambda item: item[2], reverse=True)
            for chunk, item, similarity in vector_rows[: max(limit * 4, 20)]:
                if similarity <= 0:
                    continue
                source = "pgvector" if _is_postgres(db) else "vector"
                hit = _score_row(chunk, item, terms=terms, normalized_query=normalized, retrieval_source=source, vector_score=similarity)
                key = (hit.item_id, hit.published_version, hit.chunk_index)
                previous = candidate_hits.get(key)
                candidate_hits[key] = _merge_hits(previous, hit) if previous else hit
                source_counts["vector"] += 1
                if source == "pgvector":
                    source_counts["pgvector"] += 1
        except Exception as exc:
            vector_degraded = type(exc).__name__
            if not settings.knowledge_vector_fallback_allowed:
                latency_ms = int((time.monotonic() - started) * 1000)
                return _vector_fail_closed_result(
                    normalized=normalized,
                    terms=terms,
                    filters=filters,
                    source_counts=source_counts,
                    settings=settings,
                    vector_degraded=vector_degraded,
                    latency_ms=latency_ms,
                )
    else:
        vector_degraded = "disabled"
        if not settings.knowledge_vector_fallback_allowed:
            latency_ms = int((time.monotonic() - started) * 1000)
            return _vector_fail_closed_result(
                normalized=normalized,
                terms=terms,
                filters=filters,
                source_counts=source_counts,
                settings=settings,
                vector_degraded=vector_degraded,
                latency_ms=latency_ms,
            )

    fused = _rrf_fuse(list(candidate_hits.values()))
    fused = _apply_answerability_policy(fused, normalized_query=normalized)
    fused.sort(key=lambda hit: (_country_rank(hit, country), _authority_rank(hit), -hit.score, hit.metadata.get("priority") or 10000, hit.item_key, hit.chunk_index))
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
            "storage": "pgvector" if _is_postgres(db) else "json_vector_fallback",
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
    tenant_id: str,
    brand_id: str,
    country_scope: str,
    channel_scope: str,
    market_id: int | None,
    channel: str | None,
    audience_scope: str,
    language: str | None,
) -> list[tuple[KnowledgeChunk, KnowledgeItem]]:
    if _is_postgres(db):
        return _postgres_candidate_rows(db, terms=terms, normalized_query=normalized_query, tenant_id=tenant_id, brand_id=brand_id, country_scope=country_scope, channel_scope=channel_scope, market_id=market_id, channel=channel, audience_scope=audience_scope, language=language)
    now = utc_now()
    query = (
        db.query(KnowledgeChunk, KnowledgeItem)
        .join(KnowledgeItem, KnowledgeItem.id == KnowledgeChunk.item_id)
        .filter(
            KnowledgeChunk.status == "active",
            KnowledgeItem.status == "active",
            KnowledgeChunk.published_version > 0,
            KnowledgeChunk.published_version == KnowledgeItem.published_version,
            KnowledgeChunk.tenant_id == tenant_id,
            KnowledgeItem.tenant_id == tenant_id,
            KnowledgeChunk.brand_id == brand_id,
            KnowledgeItem.brand_id == brand_id,
            KnowledgeChunk.country_scope.in_((country_scope, GLOBAL_COUNTRY_SCOPE)),
            KnowledgeItem.country_scope.in_((country_scope, GLOBAL_COUNTRY_SCOPE)),
            KnowledgeChunk.channel_scope.in_((channel_scope, GLOBAL_CHANNEL_SCOPE)),
            KnowledgeItem.channel_scope.in_((channel_scope, GLOBAL_CHANNEL_SCOPE)),
            KnowledgeChunk.visibility == CUSTOMER_VISIBILITY,
            KnowledgeItem.visibility == CUSTOMER_VISIBILITY,
            KnowledgeChunk.shareability.in_(tuple(CUSTOMER_SHAREABILITY)),
            KnowledgeItem.shareability.in_(tuple(CUSTOMER_SHAREABILITY)),
            or_(KnowledgeChunk.starts_at.is_(None), KnowledgeChunk.starts_at <= now),
            or_(KnowledgeChunk.ends_at.is_(None), KnowledgeChunk.ends_at >= now),
            or_(KnowledgeChunk.valid_from.is_(None), KnowledgeChunk.valid_from <= now),
            or_(KnowledgeChunk.valid_until.is_(None), KnowledgeChunk.valid_until >= now),
            or_(KnowledgeItem.valid_from.is_(None), KnowledgeItem.valid_from <= now),
            or_(KnowledgeItem.valid_until.is_(None), KnowledgeItem.valid_until >= now),
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
        query = query.filter(or_(KnowledgeChunk.language.is_(None), KnowledgeChunk.language == lang, KnowledgeChunk.language == "mixed", KnowledgeChunk.language.like(f"{lang}-%")))
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


def _postgres_candidate_rows(
    db: Session,
    *,
    terms: list[str],
    normalized_query: str,
    tenant_id: str,
    brand_id: str,
    country_scope: str,
    channel_scope: str,
    market_id: int | None,
    channel: str | None,
    audience_scope: str,
    language: str | None,
) -> list[tuple[KnowledgeChunk, KnowledgeItem]]:
    rows_by_id: dict[int, tuple[KnowledgeChunk, KnowledgeItem]] = {}
    for chunk, item in _structured_exact_rows(db, terms=terms, normalized_query=normalized_query, tenant_id=tenant_id, brand_id=brand_id, country_scope=country_scope, channel_scope=channel_scope, market_id=market_id, channel=channel, audience_scope=audience_scope, language=language):
        rows_by_id[chunk.id] = (chunk, item)
    search_ids = _postgres_fts_ids(db, query_text=normalized_query or " ".join(terms), tenant_id=tenant_id, brand_id=brand_id, country_scope=country_scope, channel_scope=channel_scope, market_id=market_id, channel=channel, audience_scope=audience_scope, language=language)
    if search_ids:
        ordered = _rows_by_chunk_ids(db, search_ids)
        rows_by_id.update({chunk.id: (chunk, item) for chunk, item in ordered})
    return list(rows_by_id.values())[:320]


def _structured_exact_rows(
    db: Session,
    *,
    terms: list[str],
    normalized_query: str,
    tenant_id: str,
    brand_id: str,
    country_scope: str,
    channel_scope: str,
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
            KnowledgeChunk.tenant_id == tenant_id,
            KnowledgeItem.tenant_id == tenant_id,
            KnowledgeChunk.brand_id == brand_id,
            KnowledgeItem.brand_id == brand_id,
            KnowledgeChunk.country_scope.in_((country_scope, GLOBAL_COUNTRY_SCOPE)),
            KnowledgeItem.country_scope.in_((country_scope, GLOBAL_COUNTRY_SCOPE)),
            KnowledgeChunk.channel_scope.in_((channel_scope, GLOBAL_CHANNEL_SCOPE)),
            KnowledgeItem.channel_scope.in_((channel_scope, GLOBAL_CHANNEL_SCOPE)),
            KnowledgeChunk.visibility == CUSTOMER_VISIBILITY,
            KnowledgeItem.visibility == CUSTOMER_VISIBILITY,
            KnowledgeChunk.shareability.in_(tuple(CUSTOMER_SHAREABILITY)),
            KnowledgeItem.shareability.in_(tuple(CUSTOMER_SHAREABILITY)),
            or_(KnowledgeChunk.starts_at.is_(None), KnowledgeChunk.starts_at <= now),
            or_(KnowledgeChunk.ends_at.is_(None), KnowledgeChunk.ends_at >= now),
            or_(KnowledgeChunk.valid_from.is_(None), KnowledgeChunk.valid_from <= now),
            or_(KnowledgeChunk.valid_until.is_(None), KnowledgeChunk.valid_until >= now),
            or_(KnowledgeItem.valid_from.is_(None), KnowledgeItem.valid_from <= now),
            or_(KnowledgeItem.valid_until.is_(None), KnowledgeItem.valid_until >= now),
            KnowledgeItem.knowledge_kind.in_(tuple(STRUCTURED_KINDS)),
            KnowledgeItem.fact_status == "approved",
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
        query = query.filter(or_(KnowledgeChunk.language.is_(None), KnowledgeChunk.language == lang, KnowledgeChunk.language == "mixed", KnowledgeChunk.language.like(f"{lang}-%")))
    needles = [normalized_query, *terms][:25]
    predicates = []
    for term in needles:
        if not term:
            continue
        needle = f"%{term}%"
        predicates.extend([
            KnowledgeItem.item_key.ilike(needle),
            KnowledgeItem.title.ilike(needle),
            KnowledgeItem.fact_question.ilike(needle),
            KnowledgeItem.fact_answer.ilike(needle),
            KnowledgeChunk.normalized_text.ilike(needle),
        ])
    if predicates:
        query = query.filter(or_(*predicates))
    return query.order_by(KnowledgeItem.priority.asc(), KnowledgeChunk.priority.asc(), KnowledgeChunk.chunk_index.asc()).limit(80).all()


def _postgres_fts_ids(
    db: Session,
    *,
    query_text: str,
    tenant_id: str,
    brand_id: str,
    country_scope: str,
    channel_scope: str,
    market_id: int | None,
    channel: str | None,
    audience_scope: str,
    language: str | None,
) -> list[int]:
    if not query_text.strip():
        return []
    sql, params = _postgres_candidate_sql(
        vector=False,
        tenant_id=tenant_id,
        brand_id=brand_id,
        country_scope=country_scope,
        channel_scope=channel_scope,
        market_id=market_id,
        channel=channel,
        audience_scope=audience_scope,
        language=language,
    )
    params["query_text"] = query_text[:512]
    rows = db.execute(text(sql), params).mappings().all()
    return [int(row["chunk_id"]) for row in rows]


def _rows_by_chunk_ids(db: Session, ids: list[int]) -> list[tuple[KnowledgeChunk, KnowledgeItem]]:
    if not ids:
        return []
    ordering = {chunk_id: index for index, chunk_id in enumerate(ids)}
    rows = (
        db.query(KnowledgeChunk, KnowledgeItem)
        .join(KnowledgeItem, KnowledgeItem.id == KnowledgeChunk.item_id)
        .filter(KnowledgeChunk.id.in_(ids))
        .all()
    )
    return sorted(rows, key=lambda row: ordering.get(row[0].id, 10_000))


def _vector_rows(
    db: Session,
    *,
    query_embedding: list[float],
    fallback_rows: list[tuple[KnowledgeChunk, KnowledgeItem]],
    tenant_id: str,
    brand_id: str,
    country_scope: str,
    channel_scope: str,
    market_id: int | None,
    channel: str | None,
    audience_scope: str,
    language: str | None,
    limit: int,
) -> list[tuple[KnowledgeChunk, KnowledgeItem, float]]:
    if _is_postgres(db):
        ids_and_scores = _postgres_vector_ids(db, query_embedding=query_embedding, tenant_id=tenant_id, brand_id=brand_id, country_scope=country_scope, channel_scope=channel_scope, market_id=market_id, channel=channel, audience_scope=audience_scope, language=language, limit=max(limit * 8, 40))
        rows_by_id = {chunk.id: (chunk, item) for chunk, item in _rows_by_chunk_ids(db, [chunk_id for chunk_id, _score in ids_and_scores])}
        return [(rows_by_id[chunk_id][0], rows_by_id[chunk_id][1], score) for chunk_id, score in ids_and_scores if chunk_id in rows_by_id]
    return [
        (chunk, item, cosine_similarity(query_embedding, chunk.embedding))
        for chunk, item in fallback_rows
        if isinstance(chunk.embedding, list) and chunk.embedding
    ]


def _postgres_vector_ids(
    db: Session,
    *,
    query_embedding: list[float],
    tenant_id: str,
    brand_id: str,
    country_scope: str,
    channel_scope: str,
    market_id: int | None,
    channel: str | None,
    audience_scope: str,
    language: str | None,
    limit: int,
) -> list[tuple[int, float]]:
    sql, params = _postgres_candidate_sql(
        vector=True,
        tenant_id=tenant_id,
        brand_id=brand_id,
        country_scope=country_scope,
        channel_scope=channel_scope,
        market_id=market_id,
        channel=channel,
        audience_scope=audience_scope,
        language=language,
        limit=limit,
    )
    params["query_vector"] = vector_literal(query_embedding)
    rows = db.execute(text(sql), params).mappings().all()
    return [(int(row["chunk_id"]), max(0.0, 1.0 - float(row["distance"]))) for row in rows]


def _postgres_candidate_sql(
    *,
    vector: bool,
    tenant_id: str,
    brand_id: str,
    country_scope: str,
    channel_scope: str,
    market_id: int | None,
    channel: str | None,
    audience_scope: str,
    language: str | None,
    limit: int = 320,
) -> tuple[str, dict[str, Any]]:
    params: dict[str, Any] = {
        "tenant_id": tenant_id,
        "brand_id": brand_id,
        "country_scope": country_scope,
        "global_country_scope": GLOBAL_COUNTRY_SCOPE,
        "channel_scope": channel_scope,
        "global_channel_scope": GLOBAL_CHANNEL_SCOPE,
        "audience_scope": audience_scope,
        "limit": limit,
    }
    filters = [
        "kc.status = 'active'",
        "ki.status = 'active'",
        "kc.published_version > 0",
        "kc.published_version = ki.published_version",
        "kc.tenant_id = :tenant_id",
        "ki.tenant_id = :tenant_id",
        "kc.brand_id = :brand_id",
        "ki.brand_id = :brand_id",
        "kc.country_scope IN (:country_scope, :global_country_scope)",
        "ki.country_scope IN (:country_scope, :global_country_scope)",
        "kc.channel_scope IN (:channel_scope, :global_channel_scope)",
        "ki.channel_scope IN (:channel_scope, :global_channel_scope)",
        "kc.visibility = 'customer'",
        "ki.visibility = 'customer'",
        "kc.shareability IN ('customer_visible', 'runtime_context')",
        "ki.shareability IN ('customer_visible', 'runtime_context')",
        "(kc.starts_at IS NULL OR kc.starts_at <= now())",
        "(kc.ends_at IS NULL OR kc.ends_at >= now())",
        "(kc.valid_from IS NULL OR kc.valid_from <= now())",
        "(kc.valid_until IS NULL OR kc.valid_until >= now())",
        "(ki.valid_from IS NULL OR ki.valid_from <= now())",
        "(ki.valid_until IS NULL OR ki.valid_until >= now())",
        "(ki.knowledge_kind IS NULL OR ki.knowledge_kind NOT IN ('faq', 'business_fact') OR ki.fact_status = 'approved')",
        "ki.item_key NOT ILIKE '%probe%'",
        "kc.item_key NOT ILIKE '%probe%'",
        "ki.title NOT LIKE '[PROBE]%'",
        "kc.title NOT LIKE '[PROBE]%'",
        "(ki.citation_metadata_json IS NULL OR CAST(ki.citation_metadata_json AS TEXT) NOT ILIKE '%probe_category%')",
        "(kc.metadata_json IS NULL OR CAST(kc.metadata_json AS TEXT) NOT ILIKE '%probe_category%')",
        "(kc.metadata_json IS NULL OR CAST(kc.metadata_json AS TEXT) NOT ILIKE '%probe_seed%')",
        "kc.audience_scope = :audience_scope",
    ]
    if market_id is not None:
        filters.append("(kc.market_id IS NULL OR kc.market_id = :market_id)")
        params["market_id"] = market_id
    if channel:
        filters.append("(kc.channel IS NULL OR kc.channel = :channel)")
        params["channel"] = channel.strip()
    if language:
        filters.append("(kc.language IS NULL OR kc.language = :language OR kc.language = 'mixed' OR kc.language LIKE :language_prefix)")
        params["language"] = language.strip().lower()
        params["language_prefix"] = f"{language.strip().lower()}-%"
    where = " AND ".join(filters)
    if vector:
        sql = f"""
            SELECT kc.id AS chunk_id, (kc.embedding_vector <=> CAST(:query_vector AS vector)) AS distance
            FROM knowledge_chunks kc
            JOIN knowledge_items ki ON ki.id = kc.item_id
            WHERE {where} AND kc.embedding_vector IS NOT NULL
            ORDER BY kc.embedding_vector <=> CAST(:query_vector AS vector), ki.priority ASC, kc.priority ASC
            LIMIT :limit
        """
    else:
        sql = f"""
            WITH q AS (SELECT websearch_to_tsquery('simple', :query_text) AS query)
            SELECT kc.id AS chunk_id, ts_rank_cd(kc.search_tsvector, q.query) AS rank
            FROM knowledge_chunks kc
            JOIN knowledge_items ki ON ki.id = kc.item_id
            CROSS JOIN q
            WHERE {where} AND kc.search_tsvector @@ q.query
            ORDER BY rank DESC, ki.priority ASC, kc.priority ASC
            LIMIT :limit
        """
    return sql, params


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
    structured_fact_answer = (
        (item.fact_answer or "").strip()
        if structured and (item.answer_mode or "guided_answer") in {"direct_answer", "guided_answer"}
        else None
    )
    breakdown: dict[str, float] = {}
    methods: set[str] = {retrieval_source}
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
        "tenant_id": chunk.tenant_id or item.tenant_id,
        "brand_id": chunk.brand_id or item.brand_id,
        "country_scope": (chunk.country_scope or item.country_scope or GLOBAL_COUNTRY_SCOPE).upper(),
        "channel_scope": chunk.channel_scope or item.channel_scope or GLOBAL_CHANNEL_SCOPE,
        "locale": chunk.locale or item.locale or chunk.language or item.language,
        "visibility": chunk.visibility or item.visibility,
        "shareability": chunk.shareability or item.shareability,
        "authority_level": chunk.authority_level or item.authority_level or "faq",
        "risk_level": chunk.risk_level or item.risk_level or "low",
        "review_due_at": chunk.review_due_at.isoformat() if chunk.review_due_at else (item.review_due_at.isoformat() if item.review_due_at else None),
        "knowledge_kind": item.knowledge_kind,
        "fact_status": item.fact_status,
        "answer_mode": item.answer_mode,
        "citation": item.citation_metadata_json or metadata.get("citation") or {},
        "retrieval_method": "+".join(sorted(methods)),
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
        retrieval_method="+".join(sorted(methods)),
        matched_terms=matched[:16],
        score_breakdown=breakdown,
        direct_answer=structured_fact_answer,
        answer_mode=item.answer_mode,
        source_metadata={
            "item_key": item.item_key,
            "title": item.title,
            "published_version": item.published_version,
            "chunk_index": chunk.chunk_index,
            "tenant_id": chunk.tenant_id or item.tenant_id,
            "brand_id": chunk.brand_id or item.brand_id,
            "country_scope": (chunk.country_scope or item.country_scope or GLOBAL_COUNTRY_SCOPE).upper(),
            "channel_scope": chunk.channel_scope or item.channel_scope or GLOBAL_CHANNEL_SCOPE,
            "authority_level": chunk.authority_level or item.authority_level or "faq",
            "risk_level": chunk.risk_level or item.risk_level or "low",
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


def _scope_value(value: str | None, default: str) -> str:
    cleaned = str(value or "").strip()
    return cleaned or default


def _country_scope(value: str | None) -> str:
    cleaned = _scope_value(value, GLOBAL_COUNTRY_SCOPE).upper()
    if cleaned in {"*", "ALL", "ANY"}:
        return GLOBAL_COUNTRY_SCOPE
    return cleaned[:16]


def _country_rank(hit: KnowledgeRuntimeHit, requested: str) -> int:
    scope = str(hit.metadata.get("country_scope") or GLOBAL_COUNTRY_SCOPE).upper()
    if requested != GLOBAL_COUNTRY_SCOPE and scope == requested:
        return 0
    if scope == GLOBAL_COUNTRY_SCOPE:
        return 1
    return 2


def _authority_rank(hit: KnowledgeRuntimeHit) -> int:
    return AUTHORITY_RANK.get(str(hit.metadata.get("authority_level") or "faq"), 9)


def _apply_answerability_policy(hits: list[KnowledgeRuntimeHit], *, normalized_query: str) -> list[KnowledgeRuntimeHit]:
    if not hits:
        return []
    if not _looks_high_risk_policy_query(normalized_query):
        return hits
    gated: list[KnowledgeRuntimeHit] = []
    for hit in hits:
        authority = str(hit.metadata.get("authority_level") or "faq")
        if authority in {"tool", "official_policy", "policy"} and hit.score >= MIN_HIGH_RISK_SCORE:
            gated.append(hit)
    return gated


def _looks_high_risk_policy_query(value: str) -> bool:
    lowered = value.lower()
    return any(term in lowered for term in HIGH_RISK_TERMS)


def _vector_fail_closed_result(
    *,
    normalized: str,
    terms: list[str],
    filters: dict[str, Any],
    source_counts: dict[str, int],
    settings: Any,
    vector_degraded: str,
    latency_ms: int,
) -> KnowledgeRuntimeResult:
    trace = {
        "retrieval": "hybrid_rag_v2",
        "filters": filters,
        "query": {"normalized": normalized, "terms": terms},
        "candidates_by_source": source_counts,
        "fusion": "reciprocal_rank_fusion",
        "rerank": {"strategy": "deterministic_policy_v1", "top_item_keys": []},
        "evidence_selected": [],
        "retrieval_methods": ["vector_degraded"],
        "vector": {
            "enabled": settings.knowledge_embeddings_enabled,
            "provider": settings.knowledge_embedding_provider,
            "model": settings.knowledge_embedding_model,
            "dim": settings.knowledge_embedding_dim,
            "storage": "unavailable",
            "degraded_reason": vector_degraded,
            "fallback_allowed": False,
        },
        "no_answer_reason": "vector_retrieval_unavailable",
        "latency_ms": latency_ms,
    }
    return KnowledgeRuntimeResult(
        hits=[],
        direct_facts=[],
        locked_facts=[],
        confidence=0.0,
        no_answer_reason="vector_retrieval_unavailable",
        trace=trace,
        retrieval_methods=["vector_degraded"],
        latency_ms=latency_ms,
    )


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


def _is_postgres(db: Session) -> bool:
    return db.get_bind().dialect.name == "postgresql"
