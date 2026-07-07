from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from sqlalchemy import text, or_
from sqlalchemy.orm import Session

from ..models_control_plane import KnowledgeChunk, KnowledgeItem
from ..settings import get_settings
from ..utils.time import utc_now
from .knowledge_document_service import normalize_document_text

MAX_CHUNK_CHARS = 900
CHUNK_OVERLAP_CHARS = 120
MAX_QUERY_TERMS = 24
MAX_CANDIDATES = 260
DIRECT_ANSWER_SCORE_THRESHOLD = 24.0

STRUCTURED_KINDS = {"faq", "business_fact"}
SAFE_SOURCE_FIELDS = (
    "source_type",
    "file_name",
    "market_id",
    "channel",
    "audience_scope",
    "language",
    "priority",
    "published_at",
    "knowledge_kind",
    "fact_status",
    "answer_mode",
)

EN_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "could", "do", "does", "for", "from",
    "have", "how", "i", "if", "in", "is", "it", "my", "of", "on", "or", "our", "please", "the",
    "this", "to", "we", "what", "when", "where", "will", "with", "you", "your",
}
CJK_STOPWORDS = {"一下", "一个", "什么", "怎么", "可以", "请问", "如果", "这个", "那个", "我们", "你们", "客户"}
SERVICE_TERMS = {
    "address change", "address", "delivery", "dispatch", "pickup", "reattempt", "redelivery", "return",
    "customs", "sla", "price", "fee", "refusal", "compensation", "refund", "driver phone", "tracking",
    "tracking number format", "waybill format", "wrong tracking number", "waybill not found", "tracking lookup failed",
    "改地址", "地址", "派送", "配送", "发出", "揽收", "重派", "改派", "退回", "清关", "时效", "费用",
    "价格", "拒收", "赔偿", "退款", "司机电话", "运单", "物流", "包裹", "快递", "运单号格式", "单号格式", "查不到单号",
    "多输", "少输", "核对单号",
}
BUSINESS_TERMS = {
    "sop", "policy", "faq", "contract", "rule", "sla", "support", "handoff", "business fact",
    "业务", "规则", "政策", "合同", "客服", "人工", "常见问题", "事实",
}
COUNTRY_TERMS = {
    "china", "chinese", "switzerland", "swiss", "zurich", "germany", "france", "italy", "spain", "uk",
    "us", "usa", "nigeria", "ghana", "kenya", "morocco", "egypt", "saudi", "uae", "pakistan",
    "中国", "瑞士", "德国", "法国", "意大利", "西班牙", "英国", "美国", "尼日利亚", "加纳", "肯尼亚",
    "摩洛哥", "埃及", "沙特", "阿联酋", "巴基斯坦",
}
INTENT_ALIASES = {
    "address_change": {"change address", "address change", "correct address", "update address", "改地址", "地址变更", "改派"},
    "price": {"price", "fee", "cost", "charge", "how much", "多少钱", "费用", "价格", "收费"},
    "sla": {"sla", "delivery time", "deadline", "how long", "时效", "多久", "几天", "承诺时间"},
    "reattempt": {"reattempt", "redelivery", "deliver again", "重新派送", "重派", "再派送"},
    "refusal": {"refuse", "refusal", "reject parcel", "拒收"},
    "compensation": {"compensation", "refund", "claim", "赔偿", "理赔", "退款"},
    "driver_phone": {"driver phone", "courier phone", "司机电话", "快递员电话"},
    "tracking": {"tracking", "track", "waybill", "parcel status", "物流", "运单", "单号", "查件"},
    "tracking_number_format": {
        "tracking number format", "waybill format", "wrong tracking number", "waybill not found",
        "tracking lookup failed", "invalid tracking number", "check tracking number",
        "运单号格式", "单号格式", "订单号输错", "运单号查不到", "查不到单号", "多输", "少输", "核对单号",
    },
}
COMPILED_CJK_TERMS = sorted(
    {term for term in (*SERVICE_TERMS, *BUSINESS_TERMS, *COUNTRY_TERMS) if any("\u4e00" <= ch <= "\u9fff" for ch in term)},
    key=len,
    reverse=True,
)


@dataclass(frozen=True)
class QueryAnalysis:
    language: str
    normalized_query: str
    entity_terms: list[str] = field(default_factory=list)
    service_terms: list[str] = field(default_factory=list)
    numeric_terms: list[str] = field(default_factory=list)
    intent_terms: list[str] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)
    high_value_terms: list[str] = field(default_factory=list)
    fallback_ngrams: list[str] = field(default_factory=list)

    def as_trace(self) -> dict[str, Any]:
        return asdict(self)


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
    retrieval_method: str | None = None
    matched_terms: list[str] = field(default_factory=list)
    score_breakdown: dict[str, float] = field(default_factory=dict)
    direct_answer: str | None = None
    answer_mode: str | None = None
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class KnowledgeRetrievalResult:
    hits: list[KnowledgeChunkHit]
    total: int
    query_analysis: QueryAnalysis
    candidate_count: int
    top_hits: list[dict[str, Any]]
    grounding_would_apply: bool
    grounding_source: dict[str, Any] | None
    runtime_trace: dict[str, Any] | None = None
    retrieval_methods: list[str] = field(default_factory=list)
    no_answer_reason: str | None = None
    latency_ms: int | None = None

    def as_trace(self) -> dict[str, Any]:
        if self.runtime_trace:
            return self.runtime_trace
        return {
            "query_analysis": self.query_analysis.as_trace(),
            "candidate_count": self.candidate_count,
            "total_matches": self.total,
            "top_hits": self.top_hits,
            "grounding_would_apply": self.grounding_would_apply,
            "grounding_source": self.grounding_source,
            "retrieval": "legacy_keyword_v1",
        }


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

    source_text = _index_source_text(item)
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
                tenant_id=item.tenant_id or "default",
                brand_id=item.brand_id or "default",
                country_scope=(item.country_scope or "GLOBAL").upper(),
                channel_scope=item.channel_scope or item.channel or "all",
                locale=item.locale or item.language,
                visibility=item.visibility or "customer",
                shareability=item.shareability or "customer_visible",
                authority_level=item.authority_level or "faq",
                risk_level=item.risk_level or "low",
                review_due_at=item.review_due_at,
                valid_from=item.valid_from or item.starts_at,
                valid_until=item.valid_until or item.ends_at,
                market_id=item.market_id,
                channel=item.channel,
                audience_scope=item.audience_scope,
                language=item.language,
                starts_at=item.starts_at,
                ends_at=item.ends_at,
                status=item.status,
                priority=item.priority,
                source_type=item.source_type,
                knowledge_kind=item.knowledge_kind or "document",
                fact_status=item.fact_status or "draft",
                answer_mode=item.answer_mode or "guided_answer",
                file_name=item.file_name,
                metadata_json={
                    "source_type": item.source_type,
                    "file_name": item.file_name,
                    "tenant_id": item.tenant_id or "default",
                    "brand_id": item.brand_id or "default",
                    "country_scope": (item.country_scope or "GLOBAL").upper(),
                    "channel_scope": item.channel_scope or item.channel or "all",
                    "locale": item.locale or item.language,
                    "visibility": item.visibility or "customer",
                    "shareability": item.shareability or "customer_visible",
                    "authority_level": item.authority_level or "faq",
                    "risk_level": item.risk_level or "low",
                    "audience_scope": item.audience_scope,
                    "channel": item.channel,
                    "market_id": item.market_id,
                    "language": item.language,
                    "priority": item.priority,
                    "published_at": item.published_at.isoformat() if item.published_at else None,
                    "knowledge_kind": item.knowledge_kind or "document",
                    "fact_status": item.fact_status or "draft",
                    "answer_mode": item.answer_mode or "guided_answer",
                    "fact_question": item.fact_question,
                    "fact_answer": item.fact_answer,
                    "fact_aliases": item.fact_aliases_json or [],
                    "citation": item.citation_metadata_json or {},
                },
                search_vector=normalized,
                lexical_config="simple",
                embedding_status="pending",
                retrieval_metadata_json={
                    "runtime": "hybrid_rag_v2",
                    "chunk_type": "structured_fact" if (item.knowledge_kind or "document") in STRUCTURED_KINDS else "paragraph",
                    "source_document_id": item.file_storage_key or item.item_key,
                },
                section_path=item.title,
                chunk_type="structured_fact" if (item.knowledge_kind or "document") in STRUCTURED_KINDS else "paragraph",
                source_document_id=item.file_storage_key or item.item_key,
                semantic_hash=hashlib.sha256(normalized.encode("utf-8", errors="ignore")).hexdigest(),
            )
        )

    item.indexed_version = published_version
    item.indexed_at = utc_now()
    item.chunk_count = len(chunks)
    db.flush()
    if db.get_bind().dialect.name == "postgresql":
        db.execute(
            text("""
                UPDATE knowledge_chunks
                SET search_tsvector = to_tsvector('simple', COALESCE(search_vector, normalized_text, chunk_text, ''))
                WHERE item_id = :item_id AND published_version = :published_version
            """),
            {"item_id": item.id, "published_version": published_version},
        )
    return len(chunks)


def analyze_query(value: str | None, *, language: str | None = None) -> QueryAnalysis:
    normalized = _normalize_query(value)
    detected_language = _detect_language(normalized, language=language)
    ascii_terms = _ascii_terms(normalized)
    cjk_terms = _cjk_terms(normalized)
    numeric_terms = _dedupe(re.findall(r"[a-z]{0,6}\d[\da-z.-]{1,30}|\d+(?:[.,]\d+)?", normalized, flags=re.I))

    entity_terms = _matching_terms(normalized, COUNTRY_TERMS)
    service_terms = _matching_terms(normalized, SERVICE_TERMS)
    business_terms = _matching_terms(normalized, BUSINESS_TERMS)
    intent_terms: list[str] = []
    for intent, aliases in INTENT_ALIASES.items():
        if _matching_terms(normalized, aliases):
            intent_terms.append(intent)

    raw_terms = _dedupe([
        *ascii_terms,
        *cjk_terms,
        *numeric_terms,
        *entity_terms,
        *service_terms,
        *business_terms,
        *intent_terms,
    ])
    terms = [term for term in raw_terms if not _is_stopword(term)]
    fallback_ngrams = _fallback_cjk_ngrams(normalized, protected_terms=set(cjk_terms))
    high_value = _dedupe([
        *numeric_terms,
        *entity_terms,
        *service_terms,
        *business_terms,
        *intent_terms,
        *(term for term in terms if len(term) >= 3 or _contains_cjk(term)),
        *fallback_ngrams[:8],
    ])[:MAX_QUERY_TERMS]

    if not terms and fallback_ngrams:
        terms = fallback_ngrams[:MAX_QUERY_TERMS]

    return QueryAnalysis(
        language=detected_language,
        normalized_query=normalized,
        entity_terms=entity_terms,
        service_terms=service_terms,
        numeric_terms=numeric_terms,
        intent_terms=intent_terms,
        terms=terms[:MAX_QUERY_TERMS],
        high_value_terms=high_value[:MAX_QUERY_TERMS],
        fallback_ngrams=fallback_ngrams[:MAX_QUERY_TERMS],
    )


def retrieve_published_chunks(
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
) -> KnowledgeRetrievalResult:
    if get_settings().knowledge_runtime_version == "v2":
        from .knowledge_runtime_v2 import retrieve_knowledge

        runtime = retrieve_knowledge(
            db,
            query=q or "",
            tenant_key=tenant_id or "default",
            brand_id=brand_id or "default",
            country_scope=country_scope or "GLOBAL",
            channel_scope=channel_scope or channel or "all",
            market_id=market_id,
            channel=channel,
            audience_scope=audience_scope or "customer",
            language=language,
            limit=limit,
        )
        analysis = analyze_query(q, language=language)
        hits = [
            KnowledgeChunkHit(
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
            for hit in runtime.hits
        ]
        grounding_source = _grounding_source_from_hits(hits)
        top_hits = [_top_hit_trace(hit) for hit in hits[:5]]
        return KnowledgeRetrievalResult(
            hits=hits,
            total=len(hits),
            query_analysis=analysis,
            candidate_count=runtime.trace.get("candidates_by_source", {}).get("legacy_candidate", len(hits)),
            top_hits=top_hits,
            grounding_would_apply=grounding_source is not None,
            grounding_source=grounding_source,
            runtime_trace=runtime.trace,
            retrieval_methods=runtime.retrieval_methods,
            no_answer_reason=runtime.no_answer_reason,
            latency_ms=runtime.latency_ms,
        )

    analysis = analyze_query(q, language=language)
    rows = _candidate_rows(
        db,
        analysis=analysis,
        market_id=market_id,
        channel=channel,
        audience_scope=audience_scope,
        language=language,
        limit=limit,
    )
    scored = [
        _hit_from_row(chunk, item, analysis=analysis, market_id=market_id, channel=channel, language=language)
        for chunk, item in rows
    ]
    scored = [hit for hit in scored if hit.score > 0 or not analysis.terms]
    fused = _fuse_hits(scored)
    fused.sort(key=lambda hit: (-hit.score, hit.metadata.get("priority", 10000), hit.item_key, hit.chunk_index))
    hits = fused[: max(1, min(limit, 20))]
    grounding_source = _grounding_source_from_hits(hits)
    return KnowledgeRetrievalResult(
        hits=hits,
        total=len(fused),
        query_analysis=analysis,
        candidate_count=len(rows),
        top_hits=[_top_hit_trace(hit) for hit in hits[:5]],
        grounding_would_apply=grounding_source is not None,
        grounding_source=grounding_source,
    )


def search_published_chunks(
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
) -> tuple[list[KnowledgeChunkHit], int]:
    result = retrieve_published_chunks(
        db,
        q=q,
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
    return result.hits, result.total


def _candidate_rows(
    db: Session,
    *,
    analysis: QueryAnalysis,
    market_id: int | None,
    channel: str | None,
    audience_scope: str | None,
    language: str | None,
    limit: int,
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
            or_(
                KnowledgeItem.knowledge_kind.is_(None),
                KnowledgeItem.knowledge_kind.notin_(tuple(STRUCTURED_KINDS)),
                KnowledgeItem.fact_status == "approved",
            ),
        )
    )
    if market_id is not None:
        query = query.filter(or_(KnowledgeChunk.market_id.is_(None), KnowledgeChunk.market_id == market_id))
    if channel:
        query = query.filter(or_(KnowledgeChunk.channel.is_(None), KnowledgeChunk.channel == channel.strip()))
    if audience_scope:
        query = query.filter(KnowledgeChunk.audience_scope == audience_scope.strip())
    if language:
        lang = language.strip().lower()
        query = query.filter(or_(KnowledgeChunk.language.is_(None), KnowledgeChunk.language == lang, KnowledgeChunk.language == "mixed", KnowledgeChunk.language.like(f"{lang}-%")))

    sql_terms = _dedupe([analysis.normalized_query, *analysis.high_value_terms, *analysis.terms])[:MAX_QUERY_TERMS + 1]
    if sql_terms:
        predicates = []
        for term in sql_terms:
            if not term:
                continue
            needle = f"%{term}%"
            predicates.extend(
                [
                    KnowledgeChunk.normalized_text.ilike(needle),
                    KnowledgeChunk.title.ilike(needle),
                    KnowledgeItem.item_key.ilike(needle),
                    KnowledgeItem.title.ilike(needle),
                    KnowledgeItem.summary.ilike(needle),
                    KnowledgeItem.fact_question.ilike(needle),
                    KnowledgeItem.fact_answer.ilike(needle),
                ]
            )
        query = query.filter(or_(*predicates))

    candidate_limit = max(MAX_CANDIDATES, limit * 30)
    return (
        query.order_by(
            KnowledgeItem.priority.asc(),
            KnowledgeChunk.priority.asc(),
            KnowledgeItem.item_key.asc(),
            KnowledgeChunk.chunk_index.asc(),
        )
        .limit(candidate_limit)
        .all()
    )


def _hit_from_row(
    chunk: KnowledgeChunk,
    item: KnowledgeItem,
    *,
    analysis: QueryAnalysis,
    market_id: int | None,
    channel: str | None,
    language: str | None,
) -> KnowledgeChunkHit:
    normalized_text = _normalize_query(chunk.normalized_text or chunk.chunk_text)
    title = _normalize_query(chunk.title)
    item_key = _normalize_query(chunk.item_key)
    question = _normalize_query(item.fact_question)
    answer = _normalize_query(item.fact_answer)
    aliases = [_normalize_query(alias) for alias in (item.fact_aliases_json or []) if str(alias).strip()]
    structured = (item.knowledge_kind or "document") in STRUCTURED_KINDS and item.fact_status == "approved"
    direct = structured and (item.answer_mode or "") == "direct_answer" and bool((item.fact_answer or "").strip())
    structured_fact_answer = (
        (item.fact_answer or "").strip()
        if structured and (item.answer_mode or "guided_answer") in {"direct_answer", "guided_answer"}
        else None
    )

    fields = {
        "title": title,
        "item_key": item_key,
        "text": normalized_text,
        "question": question,
        "answer": answer,
        "aliases": " ".join(aliases),
    }
    all_text = " ".join(value for value in fields.values() if value)
    matched_terms = [term for term in analysis.high_value_terms or analysis.terms if term and term in all_text]
    breakdown: dict[str, float] = {}
    methods: set[str] = set()

    phrase = analysis.normalized_query
    if phrase and len(phrase) >= 2:
        if phrase in question or phrase in fields["aliases"]:
            _add_score(breakdown, methods, "exact_phrase_recall", 18.0)
        elif phrase in title:
            _add_score(breakdown, methods, "exact_phrase_recall", 12.0)
        elif phrase in normalized_text or phrase in answer:
            _add_score(breakdown, methods, "exact_phrase_recall", 8.0)

    if structured:
        _add_score(breakdown, methods, "structured_fact_recall", 12.0)
        if direct:
            _add_score(breakdown, methods, "direct_answer_fact", 10.0)
        if item.knowledge_kind == "business_fact":
            _add_score(breakdown, methods, "business_fact_boost", 8.0)
        elif item.knowledge_kind == "faq":
            _add_score(breakdown, methods, "approved_qa_boost", 6.0)

    for term in analysis.high_value_terms:
        if not term:
            continue
        if term in title:
            _add_score(breakdown, methods, "title_recall", 5.0)
        if term in question or term in fields["aliases"]:
            _add_score(breakdown, methods, "qa_business_recall", 4.5)
        if term in answer:
            _add_score(breakdown, methods, "answer_recall", 2.0)
        if term in normalized_text:
            _add_score(breakdown, methods, "keyword_recall", 1.0 + min(normalized_text.count(term), 3) * 0.35)
        if term in item_key:
            _add_score(breakdown, methods, "business_entity_recall", 2.5)

    for term in analysis.entity_terms + analysis.service_terms + analysis.intent_terms:
        if term and term in all_text:
            _add_score(breakdown, methods, "business_entity_recall", 2.0)

    for term in analysis.numeric_terms:
        if term and term in all_text:
            _add_score(breakdown, methods, "numeric_recall", 4.0)

    coverage_terms = analysis.high_value_terms or analysis.terms
    if coverage_terms:
        coverage = len([term for term in coverage_terms if term in all_text]) / max(len(coverage_terms), 1)
        if coverage:
            _add_score(breakdown, methods, "full_text_style_scoring", round(coverage * 8.0, 3))
        if coverage >= 0.75:
            _add_score(breakdown, methods, "term_coverage_bonus", 4.0)
    else:
        _add_score(breakdown, methods, "metadata_default", max(0.0, 5.0 - min(chunk.priority or 100, 1000) / 200.0))

    if market_id is not None and chunk.market_id == market_id:
        _add_score(breakdown, methods, "metadata_market_exact", 0.75)
    if channel and chunk.channel == channel:
        _add_score(breakdown, methods, "metadata_channel_exact", 0.75)
    if language and chunk.language == language:
        _add_score(breakdown, methods, "metadata_language_exact", 0.75)

    priority = int(chunk.priority or item.priority or 100)
    _add_score(breakdown, methods, "priority_boost", max(0.0, 6.0 - min(priority, 600) / 100.0))

    score = round(sum(breakdown.values()), 3)
    metadata = _metadata_for_hit(chunk=chunk, item=item, methods=methods, matched_terms=matched_terms, breakdown=breakdown)
    return KnowledgeChunkHit(
        item_id=chunk.item_id,
        item_key=chunk.item_key,
        title=chunk.title,
        published_version=chunk.published_version,
        chunk_index=chunk.chunk_index,
        score=score,
        text=chunk.chunk_text,
        metadata=metadata,
        retrieval_method="+".join(sorted(methods)) if methods else "metadata_default",
        matched_terms=_dedupe(matched_terms),
        score_breakdown={key: round(value, 3) for key, value in sorted(breakdown.items())},
        direct_answer=structured_fact_answer,
        answer_mode=item.answer_mode,
        source_metadata=_safe_source_metadata(item, chunk),
    )


def _fuse_hits(hits: Iterable[KnowledgeChunkHit]) -> list[KnowledgeChunkHit]:
    best: dict[tuple[int, int, int], KnowledgeChunkHit] = {}
    for hit in hits:
        key = (hit.item_id, hit.published_version, hit.chunk_index)
        previous = best.get(key)
        if previous is None or hit.score > previous.score:
            best[key] = hit
        elif previous is not None and hit.score == previous.score and len(hit.matched_terms) > len(previous.matched_terms):
            best[key] = hit
    return list(best.values())


def _metadata_for_hit(
    *,
    chunk: KnowledgeChunk,
    item: KnowledgeItem,
    methods: set[str],
    matched_terms: list[str],
    breakdown: dict[str, float],
) -> dict[str, Any]:
    metadata = dict(chunk.metadata_json or {})
    metadata.update(
        {
            "source_type": chunk.source_type,
            "file_name": chunk.file_name,
            "market_id": chunk.market_id,
            "channel": chunk.channel,
            "audience_scope": chunk.audience_scope,
            "language": chunk.language,
            "priority": chunk.priority,
            "knowledge_kind": item.knowledge_kind,
            "fact_status": item.fact_status,
            "answer_mode": item.answer_mode,
            "fact_question": item.fact_question,
            "fact_aliases": item.fact_aliases_json or [],
            "citation": item.citation_metadata_json or {},
            "retrieval_method": "+".join(sorted(methods)) if methods else "metadata_default",
            "matched_terms": _dedupe(matched_terms),
            "score_breakdown": {key: round(value, 3) for key, value in sorted(breakdown.items())},
        }
    )
    return metadata


def _safe_source_metadata(item: KnowledgeItem, chunk: KnowledgeChunk) -> dict[str, Any]:
    metadata = dict(chunk.metadata_json or {})
    safe = {key: metadata.get(key) for key in SAFE_SOURCE_FIELDS if key in metadata}
    safe.update(
        {
            "item_key": item.item_key,
            "title": item.title,
            "published_version": item.published_version,
            "chunk_index": chunk.chunk_index,
            "citation": item.citation_metadata_json or metadata.get("citation") or {},
        }
    )
    return safe


def _grounding_source_from_hits(hits: list[KnowledgeChunkHit]) -> dict[str, Any] | None:
    for hit in hits:
        if hit.direct_answer and hit.score >= DIRECT_ANSWER_SCORE_THRESHOLD:
            return {
                "item_key": hit.item_key,
                "title": hit.title,
                "score": hit.score,
                "chunk_index": hit.chunk_index,
                "answer_mode": hit.answer_mode,
                "retrieval_method": hit.retrieval_method,
                "source_metadata": hit.source_metadata,
            }
    return None


def _top_hit_trace(hit: KnowledgeChunkHit) -> dict[str, Any]:
    return {
        "item_key": hit.item_key,
        "title": hit.title,
        "score": hit.score,
        "chunk_index": hit.chunk_index,
        "retrieval_method": hit.retrieval_method,
        "matched_terms": hit.matched_terms[:12],
        "score_breakdown": hit.score_breakdown,
        "answer_mode": hit.answer_mode,
        "knowledge_kind": hit.metadata.get("knowledge_kind"),
        "source_metadata": hit.source_metadata,
    }


def _index_source_text(item: KnowledgeItem) -> str:
    if (item.knowledge_kind or "document") in STRUCTURED_KINDS and ((item.fact_question or "").strip() or (item.fact_answer or "").strip()):
        aliases = [str(value).strip() for value in (item.fact_aliases_json or []) if str(value).strip()]
        parts = [
            f"Question: {(item.fact_question or '').strip()}",
            *(f"Alias: {alias}" for alias in aliases),
            f"Answer: {(item.fact_answer or '').strip()}",
        ]
        return "\n".join(part for part in parts if part.split(":", 1)[-1].strip())
    return item.published_normalized_text or item.published_body or ""


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


def _normalize_query(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = normalize_document_text(text).lower()
    return " ".join(text.split())


def _detect_language(value: str, *, language: str | None) -> str:
    if language:
        return language.strip().lower()
    cjk_count = sum(1 for ch in value if "\u4e00" <= ch <= "\u9fff")
    latin_count = sum(1 for ch in value if "a" <= ch <= "z")
    if cjk_count and latin_count:
        return "mixed"
    if cjk_count:
        return "zh"
    return "en"


def _ascii_terms(value: str) -> list[str]:
    terms = re.findall(r"[a-z][a-z0-9_-]{1,}|[0-9]+(?:[.,][0-9]+)?", value)
    return _dedupe(term for term in terms if not _is_stopword(term))


def _cjk_terms(value: str) -> list[str]:
    terms: list[str] = []
    for term in COMPILED_CJK_TERMS:
        if term in value:
            terms.append(term)
    for phrase in re.findall(r"[\u4e00-\u9fff]{2,}", value):
        if len(phrase) <= 6:
            terms.append(phrase)
    return _dedupe(term for term in terms if not _is_stopword(term))


def _fallback_cjk_ngrams(value: str, *, protected_terms: set[str]) -> list[str]:
    grams: list[str] = []
    for phrase in re.findall(r"[\u4e00-\u9fff]{3,}", value):
        for size in (2, 3, 4):
            for index in range(0, max(0, len(phrase) - size + 1)):
                gram = phrase[index:index + size]
                if gram and gram not in protected_terms and not _is_stopword(gram):
                    grams.append(gram)
    return _dedupe(grams)


def _matching_terms(value: str, terms: Iterable[str]) -> list[str]:
    matches = []
    for term in terms:
        normalized = _normalize_query(term)
        if normalized and normalized in value:
            matches.append(normalized)
    return _dedupe(matches)


def _is_stopword(term: str) -> bool:
    cleaned = term.strip().lower()
    return cleaned in EN_STOPWORDS or cleaned in CJK_STOPWORDS or len(cleaned) <= 1


def _contains_cjk(value: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in value)


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    items: list[str] = []
    for value in values:
        cleaned = str(value or "").strip().lower()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        items.append(cleaned)
    return items


def _add_score(breakdown: dict[str, float], methods: set[str], key: str, value: float) -> None:
    if value <= 0:
        return
    methods.add(key)
    breakdown[key] = breakdown.get(key, 0.0) + value
