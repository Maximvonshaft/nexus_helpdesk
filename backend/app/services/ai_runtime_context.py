from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from . import persona_service
from .knowledge_grounding_service import select_grounding_candidate
from .knowledge_retrieval_service import KnowledgeChunkHit, retrieve_published_chunks

MAX_PERSONA_SUMMARY_CHARS = 1200
MAX_PERSONA_JSON_CHARS = 1600
MAX_KNOWLEDGE_CHARS = 800
MAX_KNOWLEDGE_DIRECT_ANSWER_CHARS = 1200
MAX_CONTEXT_HITS = 5
MAX_LOCKED_FACTS = 3
MAX_IDENTITY_FIELD_CHARS = 500
MAX_IDENTITY_LIST_ITEMS = 12

_SECRET_PATTERNS = [
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.I),
    re.compile(r"sk-[A-Za-z0-9_-]{12,}", re.I),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    re.compile(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|10\.\d+\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[0-1])\.\d+\.\d+|192\.168\.\d+\.\d+)[^\s]*", re.I),
]
_INTERNAL_WORDS = {
    "provider_runtime",
    "codex_app_server",
    "codex bridge",
    "bridge token",
    "system prompt",
    "openclaw",
    "localhost",
    "127.0.0.1",
}

_IDENTITY_STRING_FIELDS = (
    "brand_name",
    "assistant_name",
    "role_label",
    "identity_statement",
    "identity_answer_rule",
    "handoff_boundary",
    "tone",
)
_IDENTITY_LIST_FIELDS = (
    "capabilities",
    "disallowed_identity_claims",
    "guardrails",
)


def build_webchat_runtime_context(
    db: Session,
    *,
    tenant_key: str,
    channel_key: str,
    body: str,
    market_id: int | None = None,
    language: str | None = None,
    audience_scope: str = "customer",
) -> dict[str, Any]:
    profile, match_rank = persona_service.resolve_preview(
        db,
        market_id=market_id,
        channel=channel_key,
        language=language,
    )
    retrieval = retrieve_published_chunks(
        db,
        q=body,
        market_id=market_id,
        channel=channel_key,
        audience_scope=audience_scope,
        language=language,
        limit=MAX_CONTEXT_HITS,
    )
    return sanitize_runtime_context({
        "context_version": "nexus_webchat_runtime_context_v2",
        "tenant_key": tenant_key,
        "metadata_filters": {
            "market_id": market_id,
            "channel": channel_key,
            "language": language,
            "audience_scope": audience_scope,
        },
        "persona_context": _persona_context(profile, match_rank),
        "knowledge_context": _knowledge_context(retrieval, query=body),
        "rag_trace": retrieval.as_trace(),
        "safety_policy": {
            "knowledge_scope": "policy_sop_faq_only",
            "locked_facts_contract": "Use locked_facts as authoritative facts. Natural rephrasing is allowed, but do not change countries, service types, timing, numbers, prices, or policy boundaries.",
            "tracking_truth_boundary": "Parcel live status requires tracking_fact_evidence_present=true and trusted tracking_fact_summary.",
            "forbidden_from_knowledge": [
                "Do not infer current parcel status from knowledge documents.",
                "Do not treat SOP, FAQ, or policy chunks as live shipment evidence.",
                "Do not override trusted tracking facts with knowledge text.",
            ],
        },
    })


def sanitize_runtime_context(value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, list):
        return [sanitize_runtime_context(item) for item in value]
    if isinstance(value, dict):
        return {str(k): sanitize_runtime_context(v) for k, v in value.items()}
    return value


def _sanitize_text(value: str) -> str:
    text = value
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[redacted]", text)
    lowered = text.lower()
    if any(word in lowered for word in _INTERNAL_WORDS):
        for word in sorted(_INTERNAL_WORDS, key=len, reverse=True):
            text = re.sub(re.escape(word), "[internal]", text, flags=re.I)
    return text


def _persona_context(profile, match_rank: int | None) -> dict[str, Any] | None:
    if profile is None or not profile.is_active or int(profile.published_version or 0) <= 0:
        return None
    content_json = profile.published_content_json or {}
    return {
        "profile_key": profile.profile_key,
        "name": profile.name,
        "summary": _clip(profile.published_summary, MAX_PERSONA_SUMMARY_CHARS),
        "content_json": _clip_json(content_json, MAX_PERSONA_JSON_CHARS),
        "identity_context": _identity_context(content_json),
        "published_version": profile.published_version,
        "match_rank": match_rank,
    }


def _identity_context(content_json: dict[str, Any]) -> dict[str, Any]:
    source: dict[str, Any] = {}
    nested = content_json.get("identity_context")
    if isinstance(nested, dict):
        source.update(nested)
    for key in (*_IDENTITY_STRING_FIELDS, *_IDENTITY_LIST_FIELDS):
        if key in content_json:
            source[key] = content_json[key]

    return {
        "brand_name": _identity_string(source.get("brand_name")),
        "assistant_name": _identity_string(source.get("assistant_name")),
        "role_label": _identity_string(source.get("role_label")),
        "identity_statement": _identity_string(source.get("identity_statement")),
        "identity_answer_rule": _identity_string(source.get("identity_answer_rule")),
        "capabilities": _identity_list(source.get("capabilities")),
        "disallowed_identity_claims": _identity_list(source.get("disallowed_identity_claims")),
        "handoff_boundary": _identity_string(source.get("handoff_boundary")),
        "tone": _identity_string(source.get("tone")),
        "guardrails": _identity_list(source.get("guardrails")),
    }


def _identity_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return _clip(" ".join(value.split()), MAX_IDENTITY_FIELD_CHARS)


def _identity_list(value: Any) -> list[str]:
    raw_items: list[Any]
    if isinstance(value, list):
        raw_items = value
    elif isinstance(value, str):
        raw_items = [value]
    else:
        return []
    items: list[str] = []
    for item in raw_items:
        if not isinstance(item, str):
            continue
        cleaned = _clip(" ".join(item.split()), MAX_IDENTITY_FIELD_CHARS)
        if cleaned:
            items.append(cleaned)
        if len(items) >= MAX_IDENTITY_LIST_ITEMS:
            break
    return items


def _knowledge_context(retrieval, *, query: str) -> dict[str, Any]:
    hits: list[KnowledgeChunkHit] = retrieval.hits
    serialized_hits = [
        {
            "item_key": hit.item_key,
            "title": hit.title,
            "published_version": hit.published_version,
            "chunk_index": hit.chunk_index,
            "score": hit.score,
            "retrieval_method": hit.retrieval_method,
            "matched_terms": hit.matched_terms,
            "score_breakdown": hit.score_breakdown,
            "direct_answer": _clip(hit.direct_answer, MAX_KNOWLEDGE_DIRECT_ANSWER_CHARS),
            "answer_mode": hit.answer_mode,
            "text": _clip(hit.text, MAX_KNOWLEDGE_CHARS),
            "metadata": {
                "source_type": hit.metadata.get("source_type"),
                "file_name": hit.metadata.get("file_name"),
                "market_id": hit.metadata.get("market_id"),
                "channel": hit.metadata.get("channel"),
                "audience_scope": hit.metadata.get("audience_scope"),
                "language": hit.metadata.get("language"),
                "knowledge_kind": hit.metadata.get("knowledge_kind"),
                "fact_status": hit.metadata.get("fact_status"),
                "answer_mode": hit.metadata.get("answer_mode"),
                "citation": hit.metadata.get("citation"),
            },
            "source_metadata": hit.source_metadata,
        }
        for hit in hits
    ]
    return {
        "retrieval": "hybrid_rag_v2",
        "total_matches": retrieval.total,
        "candidate_count": retrieval.candidate_count,
        "query_analysis": retrieval.query_analysis.as_trace(),
        "top_hits": retrieval.top_hits,
        "retrieval_methods": getattr(retrieval, "retrieval_methods", []),
        "no_answer_reason": getattr(retrieval, "no_answer_reason", None),
        "latency_ms": getattr(retrieval, "latency_ms", None),
        "grounding_would_apply": retrieval.grounding_would_apply,
        "grounding_source": retrieval.grounding_source,
        "locked_facts": _locked_facts(query=query, hits=serialized_hits, entity_terms=retrieval.query_analysis.entity_terms),
        "hits": serialized_hits,
    }


def _locked_facts(*, query: str, hits: list[dict[str, Any]], entity_terms: list[str]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for hit in hits:
        candidate = select_grounding_candidate(
            query=query,
            hits=[hit],
            tracking_fact_evidence_present=False,
            required_entity_terms=entity_terms,
        )
        if not candidate:
            continue
        source = candidate["source"]
        item_key = str(source.get("item_key") or hit.get("item_key") or "")
        if item_key in seen:
            continue
        seen.add(item_key)
        answer = _clip(str(candidate.get("answer") or ""), MAX_KNOWLEDGE_DIRECT_ANSWER_CHARS)
        if not answer:
            continue
        facts.append({
            "item_key": item_key,
            "title": hit.get("title"),
            "question": _extract_question(hit.get("text")) or hit.get("title"),
            "answer": answer,
            "answer_mode": "direct_answer",
            "source": source,
        })
        if len(facts) >= MAX_LOCKED_FACTS:
            break
    return facts


def _extract_question(text: Any) -> str | None:
    for line in str(text or "").splitlines():
        if line.lower().startswith("question:"):
            return _clip(line.split(":", 1)[1], 240)
    return None


def _clip(value: str | None, limit: int) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _clip_json(value: dict[str, Any], limit: int) -> dict[str, Any]:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if len(encoded) <= limit:
        return value
    return {"summary": encoded[: limit - 3] + "..."}
