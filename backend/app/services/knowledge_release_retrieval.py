from __future__ import annotations

import time
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..models_control_plane import KnowledgeChunk
from ..settings import get_settings
from .agent_runtime.execution_scope import released_knowledge_evidence
from .knowledge_retrieval_service import (
    KnowledgeChunkHit,
    KnowledgeRetrievalResult,
    _grounding_source_from_hits,
    _top_hit_trace,
    analyze_query,
)
from .knowledge_runtime.embeddings import cosine_similarity, get_embedding_provider
from .knowledge_runtime.runtime import (
    KNOWLEDGE_VECTOR_DIMENSION,
    _apply_answerability_policy,
    _authority_rank,
    _country_rank,
    _eligible,
    _normalize,
    _rrf_fuse,
    _score_row,
    _terms,
    validate_embedding_batch,
    validate_knowledge_vector,
)

MAX_RELEASE_KNOWLEDGE_ITEMS = 200
MAX_RELEASE_KNOWLEDGE_CHUNKS = 5000


def retrieve_release_published_chunks(
    db: Session,
    *,
    q: str | None,
    tenant_id: str | None = None,
    brand_id: str | None = None,
    country_scope: str | None = None,
    channel_scope: str | None = None,
    market_id: int | None = None,
    channel: str | None = None,
    audience_scope: str | None = "customer",
    language: str | None = None,
    limit: int = 5,
) -> KnowledgeRetrievalResult | None:
    """Search the exact immutable Knowledge versions selected by AgentRelease.

    The scoring, answerability and risk gates are the canonical Knowledge runtime
    functions. Only candidate loading changes: historical versioned chunks are
    selected directly instead of joining against the mutable current item row.
    """

    evidence = released_knowledge_evidence()
    if evidence is None:
        return None
    started = time.monotonic()
    analysis = analyze_query(q, language=language)
    if not evidence:
        return _empty_result(
            analysis=analysis,
            reason="release_has_no_knowledge",
            started=started,
            references=0,
        )
    if len(evidence) > MAX_RELEASE_KNOWLEDGE_ITEMS:
        raise RuntimeError("agent_release_knowledge_reference_limit_exceeded")

    evidence_by_version = {
        (str(item["item_key"]), int(item["version"])): item
        for item in evidence
    }
    predicates = [
        and_(
            KnowledgeChunk.item_key == item_key,
            KnowledgeChunk.published_version == version,
        )
        for item_key, version in evidence_by_version
    ]
    rows = (
        db.query(KnowledgeChunk)
        .filter(or_(*predicates))
        .order_by(
            KnowledgeChunk.priority.asc(),
            KnowledgeChunk.item_key.asc(),
            KnowledgeChunk.chunk_index.asc(),
        )
        .limit(MAX_RELEASE_KNOWLEDGE_CHUNKS + 1)
        .all()
    )
    if len(rows) > MAX_RELEASE_KNOWLEDGE_CHUNKS:
        raise RuntimeError("agent_release_knowledge_chunk_limit_exceeded")

    requested_tenant = str(tenant_id or "default").strip() or "default"
    requested_brand = str(brand_id or "default").strip() or "default"
    requested_country = str(country_scope or "GLOBAL").strip().upper() or "GLOBAL"
    requested_channel_scope = str(channel_scope or channel or "all").strip() or "all"
    requested_audience = str(audience_scope or "customer").strip() or "customer"
    normalized_query = _normalize(q or "")
    terms = _terms(normalized_query)
    now = datetime.now(timezone.utc)

    candidates: list[tuple[KnowledgeChunk, Any]] = []
    for chunk in rows:
        evidence_row = evidence_by_version.get(
            (str(chunk.item_key).strip().lower(), int(chunk.published_version or 0))
        )
        if evidence_row is None:
            continue
        item = _item_projection(evidence_row)
        if market_id is not None and chunk.market_id not in (None, market_id):
            continue
        if language and not _language_matches(chunk.language, language):
            continue
        if not _eligible(
            chunk,
            item,
            now=now,
            tenant_id=requested_tenant,
            brand_id=requested_brand,
            country_scope=requested_country,
            channel_scope=requested_channel_scope,
            channel=channel,
            audience_scope=requested_audience,
        ):
            continue
        candidates.append((chunk, item))

    query_vector, vector_degraded = _query_vector(normalized_query)
    scored = []
    for chunk, item in candidates:
        vector_score = 0.0
        if query_vector is not None and chunk.embedding_status == "embedded":
            try:
                vector_score = cosine_similarity(
                    query_vector,
                    validate_knowledge_vector(chunk.embedding),
                )
            except Exception:
                vector_score = 0.0
        source = "release_vector" if vector_score > 0 else "release_lexical"
        hit = _score_row(
            chunk,
            item,
            terms=terms,
            normalized_query=normalized_query,
            retrieval_source=source,
            vector_score=vector_score,
        )
        if hit.score > 0 or not terms:
            scored.append(hit)

    fused = _apply_answerability_policy(_rrf_fuse(scored))
    fused.sort(
        key=lambda hit: (
            _country_rank(hit, requested_country),
            _authority_rank(hit),
            -hit.score,
            hit.metadata.get("priority") or 10000,
            hit.item_key,
            hit.chunk_index,
        )
    )
    bounded_limit = max(1, min(int(limit or 5), 20))
    selected = fused[:bounded_limit]
    hits = [_public_hit(item) for item in selected]
    grounding_source = _grounding_source_from_hits(hits)
    methods = sorted(
        {
            method
            for hit in selected
            for method in str(hit.retrieval_method or "").split("+")
            if method
        }
    )
    if vector_degraded:
        methods.append("vector_degraded")
    latency_ms = max(0, int((time.monotonic() - started) * 1000))
    trace = {
        "retrieval": "hybrid_rag",
        "candidate_scope": "immutable_agent_release",
        "release_knowledge_reference_count": len(evidence),
        "release_knowledge_chunk_count": len(rows),
        "eligible_candidate_count": len(candidates),
        "query": {"normalized": normalized_query, "terms": terms},
        "evidence_selected": [_top_hit_trace(hit) for hit in hits[:8]],
        "retrieval_methods": methods,
        "vector": {
            "enabled": get_settings().knowledge_embeddings_enabled,
            "contract_dim": KNOWLEDGE_VECTOR_DIMENSION,
            "degraded_reason": vector_degraded,
        },
        "no_answer_reason": None if hits else "release_knowledge_no_match",
        "latency_ms": latency_ms,
    }
    return KnowledgeRetrievalResult(
        hits=hits,
        total=len(hits),
        query_analysis=analysis,
        candidate_count=len(candidates),
        top_hits=[_top_hit_trace(hit) for hit in hits[:5]],
        grounding_would_apply=grounding_source is not None,
        grounding_source=grounding_source,
        runtime_trace=trace,
        retrieval_methods=methods,
        no_answer_reason=None if hits else "release_knowledge_no_match",
        latency_ms=latency_ms,
    )


def _query_vector(normalized_query: str) -> tuple[list[float] | None, str | None]:
    settings = get_settings()
    if not settings.knowledge_embeddings_enabled or not normalized_query:
        return None, "disabled" if not settings.knowledge_embeddings_enabled else None
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
        return validate_embedding_batch(
            provider.embed_texts([normalized_query]),
            expected_count=1,
        )[0], None
    except Exception as exc:
        if not settings.knowledge_vector_fallback_allowed:
            raise RuntimeError("release_knowledge_vector_unavailable") from exc
        return None, type(exc).__name__


def _item_projection(evidence: dict[str, Any]) -> Any:
    snapshot = evidence.get("snapshot")
    if not isinstance(snapshot, dict):
        raise RuntimeError("agent_release_knowledge_snapshot_invalid")
    version = int(evidence.get("version") or 0)
    return SimpleNamespace(
        id=int(evidence.get("id") or 0),
        item_key=str(evidence.get("item_key") or snapshot.get("item_key") or ""),
        title=str(snapshot.get("title") or ""),
        summary=snapshot.get("summary"),
        status=str(snapshot.get("status") or "active"),
        published_version=version,
        published_at=_datetime(snapshot.get("published_at")) or datetime.now(timezone.utc),
        tenant_id=str(snapshot.get("tenant_id") or "default"),
        brand_id=str(snapshot.get("brand_id") or "default"),
        country_scope=str(snapshot.get("country_scope") or "GLOBAL"),
        channel_scope=str(snapshot.get("channel_scope") or snapshot.get("channel") or "all"),
        locale=snapshot.get("locale") or snapshot.get("language"),
        visibility=str(snapshot.get("visibility") or "customer"),
        shareability=str(snapshot.get("shareability") or "customer_visible"),
        authority_level=str(snapshot.get("authority_level") or "faq"),
        risk_level=str(snapshot.get("risk_level") or "low"),
        review_due_at=_datetime(snapshot.get("review_due_at")),
        valid_from=_datetime(snapshot.get("valid_from")),
        valid_until=_datetime(snapshot.get("valid_until")),
        starts_at=_datetime(snapshot.get("starts_at")),
        ends_at=_datetime(snapshot.get("ends_at")),
        market_id=snapshot.get("market_id"),
        channel=snapshot.get("channel"),
        audience_scope=str(snapshot.get("audience_scope") or "customer"),
        language=snapshot.get("language"),
        priority=int(snapshot.get("priority") or 100),
        knowledge_kind=str(snapshot.get("knowledge_kind") or "document"),
        fact_status=str(snapshot.get("fact_status") or "draft"),
        answer_mode=str(snapshot.get("answer_mode") or "guided_answer"),
        fact_question=snapshot.get("fact_question"),
        fact_answer=snapshot.get("fact_answer"),
        fact_aliases_json=list(snapshot.get("fact_aliases_json") or []),
        citation_metadata_json=snapshot.get("citation_metadata_json") or {},
    )


def _public_hit(hit: Any) -> KnowledgeChunkHit:
    return KnowledgeChunkHit(
        item_id=hit.item_id,
        item_key=hit.item_key,
        title=hit.title,
        published_version=hit.published_version,
        chunk_index=hit.chunk_index,
        score=hit.score,
        text=hit.text,
        metadata=hit.metadata,
        retrieval_method=hit.retrieval_method,
        matched_terms=hit.matched_terms,
        score_breakdown=hit.score_breakdown,
        direct_answer=hit.direct_answer,
        answer_mode=hit.answer_mode,
        source_metadata=hit.source_metadata,
    )


def _empty_result(
    *,
    analysis: Any,
    reason: str,
    started: float,
    references: int,
) -> KnowledgeRetrievalResult:
    latency_ms = max(0, int((time.monotonic() - started) * 1000))
    trace = {
        "retrieval": "hybrid_rag",
        "candidate_scope": "immutable_agent_release",
        "release_knowledge_reference_count": references,
        "evidence_selected": [],
        "retrieval_methods": [],
        "no_answer_reason": reason,
        "latency_ms": latency_ms,
    }
    return KnowledgeRetrievalResult(
        hits=[],
        total=0,
        query_analysis=analysis,
        candidate_count=0,
        top_hits=[],
        grounding_would_apply=False,
        grounding_source=None,
        runtime_trace=trace,
        retrieval_methods=[],
        no_answer_reason=reason,
        latency_ms=latency_ms,
    )


def _language_matches(actual: Any, requested: str) -> bool:
    value = str(actual or "").strip().lower()
    expected = str(requested or "").strip().lower()
    return not value or value == "mixed" or value == expected or value.startswith(f"{expected}-")


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None
