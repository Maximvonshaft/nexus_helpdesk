from __future__ import annotations

import contextvars
import re
from dataclasses import dataclass
from typing import Any, Callable

from . import runtime as _runtime


QUERY_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "could",
    "dear",
    "do",
    "does",
    "for",
    "from",
    "have",
    "hello",
    "help",
    "hi",
    "i",
    "if",
    "in",
    "is",
    "it",
    "me",
    "my",
    "no",
    "not",
    "of",
    "ok",
    "on",
    "or",
    "our",
    "please",
    "ready",
    "sign",
    "the",
    "this",
    "to",
    "tomorrow",
    "we",
    "what",
    "when",
    "where",
    "will",
    "with",
    "yes",
    "you",
    "your",
}
CJK_STOPWORDS = {"一下", "一个", "什么", "怎么", "可以", "请问", "如果", "这个", "那个", "我们", "你们", "客户"}
COUNTRY_CODE_TERMS = {"ch", "cn", "de", "eg", "es", "fr", "gh", "ke", "ma", "mk", "mx", "ng", "pk", "sa", "uae", "uk", "usa"}
COUNTRY_ENTITY_TERMS = {
    "china",
    "chinese",
    "switzerland",
    "swiss",
    "zurich",
    "montenegro",
    "germany",
    "france",
    "italy",
    "spain",
    "united kingdom",
    "nigeria",
    "ghana",
    "kenya",
    "morocco",
    "egypt",
    "saudi",
    "pakistan",
    "中国",
    "瑞士",
    "黑山",
    "德国",
    "法国",
    "意大利",
    "西班牙",
    "英国",
    "美国",
    "尼日利亚",
    "加纳",
    "肯尼亚",
    "摩洛哥",
    "埃及",
    "沙特",
    "巴基斯坦",
}
PROTECTED_DOMAIN_TERMS = {
    "address",
    "availability",
    "available",
    "compensation",
    "customs",
    "delivery",
    "dispatch",
    "domestic",
    "domestic-to-domestic",
    "fee",
    "parcel",
    "policy",
    "refund",
    "return",
    "service",
    "shipment",
    "sla",
    "tracking",
    "unavailable",
    "waybill",
    "地址",
    "包裹",
    "本土",
    "本对本",
    "查件",
    "费用",
    "开通",
    "派送",
    "清关",
    "瑞士国内",
    "退款",
    "物流",
    "运单",
}
SERVICE_AVAILABILITY_PHRASES = {
    "service availability",
    "available",
    "unavailable",
    "domestic to domestic",
    "domestic-to-domestic",
    "within switzerland",
    "send parcel within switzerland",
    "swiss local delivery",
    "瑞士本土",
    "瑞士国内",
    "本对本",
    "开通了吗",
    "暂未开通",
}


@dataclass(frozen=True)
class TermPolicy:
    raw_terms: list[str]
    filtered_terms: list[str]
    dropped_stopwords: list[str]
    domain_terms: list[str]
    entity_terms: list[str]
    country_terms: list[str]
    service_availability_intent: bool


_CURRENT_POLICY: contextvars.ContextVar[TermPolicy | None] = contextvars.ContextVar("knowledge_runtime_relevance_policy", default=None)
_INSTALLED = False
_ORIGINAL_RETRIEVE_KNOWLEDGE: Callable[..., Any] | None = None
_ORIGINAL_CANDIDATE_ROWS: Callable[..., Any] | None = None
_ORIGINAL_TRACE_HIT: Callable[..., Any] | None = None


def install() -> None:
    global _INSTALLED, _ORIGINAL_RETRIEVE_KNOWLEDGE, _ORIGINAL_CANDIDATE_ROWS, _ORIGINAL_TRACE_HIT
    if _INSTALLED:
        return
    _ORIGINAL_RETRIEVE_KNOWLEDGE = _runtime.retrieve_knowledge
    _ORIGINAL_CANDIDATE_ROWS = _runtime._candidate_rows
    _ORIGINAL_TRACE_HIT = _runtime._trace_hit
    _runtime.retrieve_knowledge = retrieve_knowledge_guarded
    _runtime._terms = terms_guarded
    _runtime._candidate_rows = candidate_rows_guarded
    _runtime._score_row = score_row_guarded
    _runtime._fact_from_hit = fact_from_hit_guarded
    _runtime._trace_hit = trace_hit_guarded
    _INSTALLED = True


def retrieve_knowledge_guarded(*args: Any, **kwargs: Any):
    if _ORIGINAL_RETRIEVE_KNOWLEDGE is None:
        raise RuntimeError("knowledge runtime relevance guard is not installed")
    result = _ORIGINAL_RETRIEVE_KNOWLEDGE(*args, **kwargs)
    policy = _CURRENT_POLICY.get()
    if policy:
        query_trace = dict(result.trace.get("query") or {})
        query_trace.update(
            {
                "raw_terms": policy.raw_terms,
                "filtered_terms": policy.filtered_terms,
                "dropped_stopwords": policy.dropped_stopwords,
                "domain_terms": policy.domain_terms,
                "entity_terms": policy.entity_terms,
                "country_terms": policy.country_terms,
            }
        )
        result.trace["query"] = query_trace
        result.trace["relevance_policy"] = {
            "version": "knowledge_runtime_relevance_guard_v1",
            "service_availability_intent": policy.service_availability_intent,
            "direct_answer_requires_relevance_gate": True,
            "global_direct_answer_requires_stronger_relevance": True,
        }
    return result


def terms_guarded(value: str) -> list[str]:
    policy = build_term_policy(value)
    _CURRENT_POLICY.set(policy)
    return policy.filtered_terms[:32]


def candidate_rows_guarded(db, *, terms: list[str], normalized_query: str, tenant_id: str, brand_id: str, country_scope: str, channel_scope: str, market_id: int | None, channel: str | None, audience_scope: str, language: str | None):
    if _ORIGINAL_CANDIDATE_ROWS is None:
        raise RuntimeError("knowledge runtime relevance guard is not installed")
    policy = _CURRENT_POLICY.get() or build_term_policy(normalized_query)
    if not policy.filtered_terms:
        return []
    guarded_query = normalized_query if _query_has_meaningful_content(normalized_query, policy) else " ".join(policy.filtered_terms)
    return _ORIGINAL_CANDIDATE_ROWS(
        db,
        terms=policy.filtered_terms,
        normalized_query=guarded_query,
        tenant_id=tenant_id,
        brand_id=brand_id,
        country_scope=country_scope,
        channel_scope=channel_scope,
        market_id=market_id,
        channel=channel,
        audience_scope=audience_scope,
        language=language,
    )


def score_row_guarded(chunk, item, *, terms: list[str], normalized_query: str, retrieval_source: str, vector_score: float = 0.0):
    policy = _CURRENT_POLICY.get() or build_term_policy(normalized_query)
    text_value = _normalize_for_match(
        " ".join(
            [
                chunk.title or "",
                chunk.normalized_text or chunk.chunk_text or "",
                item.title or "",
                item.summary or "",
                item.fact_question or "",
                item.fact_answer or "",
                item.item_key or "",
                " ".join(item.fact_aliases_json or []),
            ]
        )
    )
    matched = [term for term in policy.filtered_terms if _term_matches(term, text_value)]
    structured = (item.knowledge_kind or "document") in _runtime.STRUCTURED_KINDS and item.fact_status == "approved"
    answer_mode = item.answer_mode or "guided_answer"
    fact_answer = (item.fact_answer or "").strip()
    has_fact_answer = structured and answer_mode in {"direct_answer", "guided_answer"} and bool(fact_answer)
    is_direct_mode = structured and answer_mode == "direct_answer" and bool(fact_answer)
    alias_match = _alias_matches(item.fact_aliases_json or [], normalized_query)
    strong_phrase = _strong_phrase_match(normalized_query, text_value)
    item_service_availability = _looks_like_service_availability(text_value)
    intent_match = _intent_matches(policy=policy, item_service_availability=item_service_availability, matched_terms=matched, alias_match=alias_match, strong_phrase=strong_phrase)
    meaningful_count = len(set(matched))
    source_country_scope = (chunk.country_scope or item.country_scope or _runtime.GLOBAL_COUNTRY_SCOPE).upper()
    direct_answer_eligible, block_reason = _direct_answer_eligibility(
        has_fact_answer=has_fact_answer,
        is_direct_mode=is_direct_mode,
        policy=policy,
        item_service_availability=item_service_availability,
        meaningful_count=meaningful_count,
        alias_match=alias_match,
        strong_phrase=strong_phrase,
        intent_match=intent_match,
        source_country_scope=source_country_scope,
    )
    breakdown: dict[str, float] = {}
    methods: set[str] = {retrieval_source}
    lexical_signal = bool(matched or alias_match or strong_phrase)
    if structured and lexical_signal:
        breakdown["structured_exact"] = 18.0
        methods.add("structured_exact")
    if strong_phrase:
        breakdown["exact_phrase"] = 20.0
        methods.add("structured_exact" if structured else "fts")
    if matched:
        breakdown["fts"] = min(42.0, len(matched) * 5.0)
        methods.add("fts")
    if alias_match:
        breakdown["alias"] = 12.0
        methods.add("structured_exact" if structured else "fts")
    if is_direct_mode and direct_answer_eligible:
        breakdown["direct_answer"] = 14.0
        methods.add("structured_exact")
    if vector_score > 0:
        breakdown["vector"] = round(vector_score * 24.0, 3)
        methods.add("vector")
    priority = int(chunk.priority or item.priority or 100)
    if lexical_signal or vector_score > 0:
        breakdown["priority"] = max(0.0, 6.0 - min(priority, 600) / 100.0)
    score = round(sum(breakdown.values()), 3)
    metadata = dict(chunk.metadata_json or {})
    metadata.update(
        {
            "priority": priority,
            "tenant_id": chunk.tenant_id or item.tenant_id,
            "brand_id": chunk.brand_id or item.brand_id,
            "country_scope": source_country_scope,
            "channel_scope": chunk.channel_scope or item.channel_scope or _runtime.GLOBAL_CHANNEL_SCOPE,
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
            "match_type": _match_type(matched=matched, alias_match=alias_match, strong_phrase=strong_phrase, vector_score=vector_score),
            "meaningful_term_count": meaningful_count,
            "scope_match": True,
            "intent_match": intent_match,
            "direct_answer_eligible": direct_answer_eligible,
            "direct_answer_block_reason": block_reason,
            "global_fallback_used": source_country_scope == _runtime.GLOBAL_COUNTRY_SCOPE,
        }
    )
    return _runtime.KnowledgeRuntimeHit(
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
        direct_answer=fact_answer if has_fact_answer and direct_answer_eligible else None,
        answer_mode=item.answer_mode,
        source_metadata={
            "item_key": item.item_key,
            "title": item.title,
            "published_version": item.published_version,
            "chunk_index": chunk.chunk_index,
            "tenant_id": chunk.tenant_id or item.tenant_id,
            "brand_id": chunk.brand_id or item.brand_id,
            "country_scope": source_country_scope,
            "channel_scope": chunk.channel_scope or item.channel_scope or _runtime.GLOBAL_CHANNEL_SCOPE,
            "authority_level": chunk.authority_level or item.authority_level or "faq",
            "risk_level": chunk.risk_level or item.risk_level or "low",
            "citation": item.citation_metadata_json or metadata.get("citation") or {},
        },
    )


def fact_from_hit_guarded(hit) -> dict[str, Any] | None:
    if not hit.direct_answer:
        return None
    if hit.metadata.get("direct_answer_eligible") is False:
        return None
    return {"item_key": hit.item_key, "title": hit.title, "answer": hit.direct_answer, "answer_mode": hit.answer_mode, "source": hit.source_metadata, "score": hit.score}


def trace_hit_guarded(hit) -> dict[str, Any]:
    trace = _ORIGINAL_TRACE_HIT(hit) if _ORIGINAL_TRACE_HIT is not None else {"item_key": hit.item_key, "title": hit.title, "score": hit.score, "chunk_index": hit.chunk_index, "retrieval_method": hit.retrieval_method, "matched_terms": hit.matched_terms, "answer_mode": hit.answer_mode, "source_metadata": hit.source_metadata}
    trace.update(
        {
            "match_type": hit.metadata.get("match_type"),
            "direct_answer_eligible": hit.metadata.get("direct_answer_eligible"),
            "direct_answer_block_reason": hit.metadata.get("direct_answer_block_reason"),
            "global_fallback_used": hit.metadata.get("global_fallback_used"),
            "scope_match": hit.metadata.get("scope_match"),
            "intent_match": hit.metadata.get("intent_match"),
        }
    )
    return trace


def build_term_policy(value: str | None) -> TermPolicy:
    normalized = _runtime._normalize(value)
    raw_terms = _raw_terms(normalized)
    dropped: list[str] = []
    filtered: list[str] = []
    domain_terms: list[str] = []
    entity_terms: list[str] = []
    country_terms: list[str] = []
    for term in raw_terms:
        cleaned = term.strip().lower()
        if not cleaned:
            continue
        if cleaned in COUNTRY_CODE_TERMS:
            country_terms.append(cleaned)
            entity_terms.append(cleaned)
            dropped.append(cleaned)
            continue
        if _is_query_stopword(cleaned):
            dropped.append(cleaned)
            continue
        if cleaned in COUNTRY_ENTITY_TERMS:
            country_terms.append(cleaned)
            entity_terms.append(cleaned)
        if cleaned in PROTECTED_DOMAIN_TERMS or cleaned in SERVICE_AVAILABILITY_PHRASES:
            domain_terms.append(cleaned)
        filtered.append(cleaned)
    return TermPolicy(
        raw_terms=raw_terms,
        filtered_terms=_dedupe(filtered),
        dropped_stopwords=_dedupe(dropped),
        domain_terms=_dedupe(domain_terms),
        entity_terms=_dedupe(entity_terms),
        country_terms=_dedupe(country_terms),
        service_availability_intent=any(_phrase_in_text(phrase, normalized) for phrase in SERVICE_AVAILABILITY_PHRASES),
    )


def _raw_terms(normalized: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9][a-z0-9_-]{1,}|[\u4e00-\u9fff]{2,}", normalized, flags=re.I)
    cjk: list[str] = []
    for phrase in re.findall(r"[\u4e00-\u9fff]{3,}", normalized):
        cjk.extend(phrase[index : index + 2] for index in range(max(0, len(phrase) - 1)))
    phrase_hits = [phrase for phrase in SERVICE_AVAILABILITY_PHRASES if _phrase_in_text(phrase, normalized)]
    tracking_hits = [term.lower() for term in _runtime.TRACKING_TERMS if _phrase_in_text(str(term).lower(), normalized)]
    return _dedupe([*tokens, *cjk, *phrase_hits, *tracking_hits])[:48]


def _is_query_stopword(term: str) -> bool:
    if term in QUERY_STOPWORDS or term in CJK_STOPWORDS:
        return True
    if _contains_cjk(term):
        return False
    return len(term) < 3 and term not in PROTECTED_DOMAIN_TERMS


def _query_has_meaningful_content(normalized_query: str, policy: TermPolicy) -> bool:
    if len(normalized_query.strip()) < 8:
        return False
    return len(policy.filtered_terms) >= 2 or bool(policy.domain_terms or policy.entity_terms)


def _alias_matches(aliases: list[str], normalized_query: str) -> bool:
    query = _normalize_for_match(normalized_query)
    for alias in aliases:
        cleaned = _normalize_for_match(alias)
        if len(cleaned) >= 3 and (_phrase_in_text(cleaned, query) or _phrase_in_text(query, cleaned)):
            return True
    return False


def _strong_phrase_match(normalized_query: str, text_value: str) -> bool:
    query = _normalize_for_match(normalized_query)
    return len(query) >= 8 and _phrase_in_text(query, text_value)


def _intent_matches(*, policy: TermPolicy, item_service_availability: bool, matched_terms: list[str], alias_match: bool, strong_phrase: bool) -> bool:
    if item_service_availability:
        return policy.service_availability_intent and (bool(matched_terms) or alias_match or strong_phrase)
    return bool(matched_terms or alias_match or strong_phrase)


def _direct_answer_eligibility(*, has_fact_answer: bool, is_direct_mode: bool, policy: TermPolicy, item_service_availability: bool, meaningful_count: int, alias_match: bool, strong_phrase: bool, intent_match: bool, source_country_scope: str) -> tuple[bool, str | None]:
    if not has_fact_answer:
        return False, "no_fact_answer"
    if not intent_match:
        return False, "intent_mismatch"
    if item_service_availability and not policy.service_availability_intent:
        return False, "service_availability_intent_missing"
    if source_country_scope == _runtime.GLOBAL_COUNTRY_SCOPE and is_direct_mode and not (alias_match or strong_phrase or meaningful_count >= 2):
        return False, "global_direct_answer_requires_stronger_relevance"
    if strong_phrase or alias_match or meaningful_count >= 2:
        return True, None
    if item_service_availability and policy.service_availability_intent and meaningful_count >= 1 and policy.country_terms:
        return True, None
    return False, "insufficient_meaningful_match"


def _looks_like_service_availability(text_value: str) -> bool:
    return any(_phrase_in_text(phrase, text_value) for phrase in SERVICE_AVAILABILITY_PHRASES) or (_phrase_in_text("service", text_value) and (_phrase_in_text("available", text_value) or _phrase_in_text("unavailable", text_value)))


def _match_type(*, matched: list[str], alias_match: bool, strong_phrase: bool, vector_score: float) -> str:
    if strong_phrase:
        return "phrase"
    if alias_match:
        return "alias"
    if matched:
        return "token"
    if vector_score > 0:
        return "vector"
    return "none"


def _term_matches(term: str, normalized_text: str) -> bool:
    cleaned = _normalize_for_match(term)
    if not cleaned:
        return False
    if _contains_cjk(cleaned):
        return cleaned in normalized_text
    if " " in cleaned:
        return _phrase_in_text(cleaned, normalized_text)
    return cleaned in set(re.findall(r"[a-z0-9]+", normalized_text))


def _phrase_in_text(phrase: str, text: str) -> bool:
    cleaned_phrase = _normalize_for_match(phrase)
    cleaned_text = _normalize_for_match(text)
    if not cleaned_phrase or not cleaned_text:
        return False
    if _contains_cjk(cleaned_phrase):
        return cleaned_phrase in cleaned_text
    return f" {cleaned_phrase} " in f" {cleaned_text} "


def _normalize_for_match(value: str | None) -> str:
    lowered = str(value or "").lower().replace("-", " ").replace("_", " ")
    lowered = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", lowered)
    return " ".join(lowered.split())


def _contains_cjk(value: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in value)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = str(value or "").strip().lower()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result
