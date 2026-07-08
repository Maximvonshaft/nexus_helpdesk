from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from . import persona_service
from ..models import Market
from ..webchat_models import WebchatMessage
from .effective_country import effective_country_payload, resolve_effective_country
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
MAX_STRUCTURED_RECENT_CONTEXT = 12
MAX_RECENT_CONTEXT_TEXT_CHARS = 500
TRACKING_NUMBER_RE = re.compile(r"\b(?=[A-Z0-9]{8,30}\b)(?=[A-Z0-9]*\d)[A-Z0-9]+\b", re.I)
TRACKING_CONTEXT_RE = re.compile(
    r"\b(track|tracking|waybill|parcel|package|shipment|delivery|order)\b|物流|运单|单号|查件|查询|包裹|快递|订单号|订单",
    re.I,
)
TRACKING_NO_EVIDENCE_EXPANSION_TERMS = [
    "tracking lookup failed",
    "waybill not found",
    "wrong tracking number",
    "tracking number format",
    "waybill format",
    "客户输入运单号查不到",
    "订单号多输少输",
    "运单号格式",
    "核对单号",
    "CH tracking number format",
]
_TRACKING_REFERENCE_RE = re.compile(r"\b(?=[A-Z0-9-]{8,35}\b)(?=[A-Z0-9-]*\d)[A-Z0-9][A-Z0-9-]*[A-Z0-9]\b", re.I)


def _looks_like_tracking_identifier(token: str) -> bool:
    normalized = (token or "").strip().upper()
    if not normalized:
        return False
    digit_count = sum(1 for char in normalized if char.isdigit())
    letter_count = sum(1 for char in normalized if char.isalpha())
    if digit_count == len(normalized):
        return False
    if normalized.startswith("CH") and len(normalized) >= 10 and digit_count >= 6:
        return True
    return len(normalized) >= 12 and digit_count >= 6 and letter_count >= 1

_SECRET_PATTERNS = [
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.I),
    re.compile(r"sk-[A-Za-z0-9_-]{12,}", re.I),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    re.compile(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|10\.\d+\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[0-1])\.\d+\.\d+|192\.168\.\d+\.\d+)[^\s]*", re.I),
]
_INTERNAL_WORDS = {
    "provider_runtime",
    "bridge token",
    "system prompt",
    "external_channel",
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
    tracking_number: str | None = None,
    tracking_fact_evidence_present: bool | None = None,
    ticket: Any = None,
    conversation: Any = None,
    customer: Any = None,
    channel_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile, match_rank = persona_service.resolve_preview(
        db,
        market_id=market_id,
        channel=channel_key,
        language=language,
    )
    retrieval_query, expansion_terms = _runtime_retrieval_query(
        body=body,
        tracking_number=tracking_number,
        tracking_fact_evidence_present=tracking_fact_evidence_present,
    )
    country_payload = _channel_payload_with_market_country(
        db,
        channel_payload=channel_payload,
        market_id=market_id,
    )
    effective_country = resolve_effective_country(
        ticket=ticket,
        conversation=conversation,
        customer=customer,
        market_id=market_id,
        channel_payload=country_payload,
    )
    retrieval = retrieve_published_chunks(
        db,
        q=retrieval_query,
        tenant_id=tenant_key,
        brand_id="default",
        country_scope=effective_country.country,
        channel_scope=channel_key,
        market_id=market_id,
        channel=channel_key,
        audience_scope=audience_scope,
        language=language,
        limit=MAX_CONTEXT_HITS,
    )
    rag_trace = dict(retrieval.as_trace())
    rag_trace.update(effective_country_payload(effective_country))
    knowledge_context = _knowledge_context(retrieval, query=retrieval_query, original_query=body, query_expansion_terms=expansion_terms)
    structured_recent_context = build_structured_recent_context(db=db, conversation=conversation, current_body=body)
    tracking_intent_detected = _runtime_tracking_intent_detected(body=body, tracking_number=tracking_number)
    guard = build_runtime_context_guard(
        structured_recent_context=structured_recent_context,
        tracking_intent_detected=tracking_intent_detected,
        tracking_fact_evidence_present=bool(tracking_fact_evidence_present),
        kb_hits_count=len(knowledge_context.get("hits") or []),
    )
    return sanitize_runtime_context({
        "context_version": "nexus_webchat_runtime_context_v2",
        "tenant_key": tenant_key,
        "metadata_filters": {
            "market_id": market_id,
            "channel": channel_key,
            "language": language,
            "audience_scope": audience_scope,
            **effective_country_payload(effective_country),
        },
        "persona_context": _persona_context(profile, match_rank),
        "knowledge_context": knowledge_context,
        "rag_trace": rag_trace,
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
        **guard,
    })


def build_structured_recent_context(
    *,
    db: Session | None = None,
    conversation: Any = None,
    history_rows: list[Any] | None = None,
    current_message_id: int | None = None,
    current_body: str | None = None,
    limit: int = MAX_STRUCTURED_RECENT_CONTEXT,
) -> list[dict[str, Any]]:
    rows = list(history_rows or [])
    if not rows and db is not None and getattr(conversation, "id", None) is not None:
        rows = (
            db.query(WebchatMessage)
            .filter(WebchatMessage.conversation_id == conversation.id)
            .order_by(WebchatMessage.created_at.desc(), WebchatMessage.id.desc())
            .limit(limit)
            .all()
        )
        rows.reverse()
    current_body_norm = " ".join(str(current_body or "").split())
    structured: list[dict[str, Any]] = []
    skipped_current = False
    for row in rows[-limit:]:
        row_id = getattr(row, "id", None)
        if current_message_id is not None and row_id == current_message_id:
            continue
        text = _row_text(row)
        if not text:
            continue
        direction = str(getattr(row, "direction", "") or "").strip().lower()
        if not skipped_current and current_body_norm and direction == "visitor" and " ".join(text.split()) == current_body_norm:
            skipped_current = True
            continue
        if direction == "visitor":
            role = "customer"
            source = "webchat_message"
            factuality = "customer_claim"
            use = "conversation_context"
        else:
            role = "ai"
            source = "previous_ai_reply"
            factuality = "not_evidence"
            use = "coherence_only"
        structured.append({
            "role": role,
            "text": _redact_conversation_text(text),
            "source": source,
            "message_id": row_id,
            "factuality": factuality,
            "use": use,
        })
    return structured[-limit:]


def build_runtime_context_guard(
    *,
    structured_recent_context: list[dict[str, Any]] | None,
    tracking_intent_detected: bool,
    tracking_fact_evidence_present: bool,
    kb_hits_count: int,
) -> dict[str, Any]:
    recent = [item for item in (structured_recent_context or []) if isinstance(item, dict)]
    prior_ai_messages_count = sum(1 for item in recent if item.get("source") == "previous_ai_reply" or item.get("role") == "ai")
    customer_claim_count = sum(1 for item in recent if item.get("factuality") == "customer_claim" or item.get("role") == "customer")
    evidence_contract = {
        "tool_facts_present": bool(tracking_fact_evidence_present),
        "tracking_fact_evidence_present": bool(tracking_fact_evidence_present),
        "kb_hits_count": int(kb_hits_count or 0),
        "recent_context_count": len(recent),
        "prior_ai_messages_count": prior_ai_messages_count,
        "customer_claim_count": customer_claim_count,
        "memory_items_count": 0,
        "memory_system": "not_enabled",
        "support_memory_ledger_used_by_runtime": False,
    }
    if tracking_intent_detected and not tracking_fact_evidence_present:
        answer_policy = {
            "live_tracking_answer_allowed": False,
            "allowed_reply_types": ["clarifying_question", "handoff_notice", "null_reply"],
            "forbidden": [
                "Do not answer live parcel status from KB.",
                "Do not answer live parcel status from previous AI reply.",
                "Do not answer live parcel status from customer claim.",
            ],
        }
    elif tracking_intent_detected and tracking_fact_evidence_present:
        answer_policy = {
            "live_tracking_answer_allowed": True,
            "required_sources": ["tracking_tool"],
        }
    else:
        answer_policy = {
            "live_tracking_answer_allowed": bool(tracking_fact_evidence_present),
            "required_sources": ["tracking_tool"] if tracking_fact_evidence_present else [],
        }
    return {
        "structured_recent_context": recent,
        "recent_context_policy": {
            "legacy_recent_context_role_text_only": True,
            "legacy_recent_context_use": "backward_compatibility_only",
            "structured_recent_context_is_authoritative_for_factuality": True,
        },
        "context_policy": {
            "previous_ai_replies_are_not_facts": True,
            "customer_messages_are_claims_not_verified_facts": True,
            "tracking_status_requires_tool_fact": True,
            "kb_cannot_answer_live_tracking_status": True,
            "tool_result_overrides_kb": True,
            "ask_clarifying_question_when_intent_unclear": True,
        },
        "evidence_contract": evidence_contract,
        "answer_policy": answer_policy,
        "runtime_trace_context_fields": {
            "structured_recent_context_count": len(recent),
            "prior_ai_messages_count": prior_ai_messages_count,
            "customer_claim_count": customer_claim_count,
            "tracking_intent_detected": bool(tracking_intent_detected),
            "tracking_fact_evidence_present": bool(tracking_fact_evidence_present),
            "live_tracking_answer_allowed": bool(answer_policy.get("live_tracking_answer_allowed")),
            "support_memory_ledger_used_by_runtime": False,
        },
    }


def _row_text(row: Any) -> str:
    return str(getattr(row, "body_text", None) or getattr(row, "body", None) or "").strip()


def _redact_conversation_text(text: str) -> str:
    cleaned = " ".join(str(text or "").split())[:MAX_RECENT_CONTEXT_TEXT_CHARS]
    return _TRACKING_REFERENCE_RE.sub("[redacted_tracking_reference]", cleaned)


def _runtime_tracking_intent_detected(*, body: str | None, tracking_number: str | None) -> bool:
    text = str(body or "")
    if bool((tracking_number or "").strip()):
        return True
    return bool(TRACKING_CONTEXT_RE.search(text) or TRACKING_NUMBER_RE.search(text))


def _channel_payload_with_market_country(
    db: Session,
    *,
    channel_payload: dict[str, Any] | None,
    market_id: int | None,
) -> dict[str, Any]:
    payload = dict(channel_payload or {})
    if market_id is None or payload.get("market_country") or payload.get("ticket_market_country"):
        return payload
    market = db.query(Market).filter(Market.id == market_id).first()
    if market and market.country_code:
        payload["market_country"] = market.country_code
    return payload


def _runtime_retrieval_query(*, body: str, tracking_number: str | None, tracking_fact_evidence_present: bool | None) -> tuple[str, list[str]]:
    if tracking_fact_evidence_present is True:
        return body, []
    text = body or ""
    has_tracking_language = bool(TRACKING_CONTEXT_RE.search(text))
    token_match = TRACKING_NUMBER_RE.search(text)
    has_tracking_identifier = bool((tracking_number or "").strip())
    if token_match:
        token = token_match.group(0)
        token_is_digit_only = token.isdigit()
        token_is_reference_only = text.strip() == token
        token_looks_like_tracking = _looks_like_tracking_identifier(token)
        has_tracking_identifier = has_tracking_identifier or (
            token_is_reference_only
            or has_tracking_language
            or (not token_is_digit_only and token_looks_like_tracking)
        )
    if not has_tracking_identifier and not has_tracking_language:
        return body, []
    terms = TRACKING_NO_EVIDENCE_EXPANSION_TERMS
    return " ".join(part for part in [body, tracking_number, *terms] if part), terms


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


def _knowledge_context(retrieval, *, query: str, original_query: str | None = None, query_expansion_terms: list[str] | None = None) -> dict[str, Any]:
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
                "tenant_id": hit.metadata.get("tenant_id"),
                "brand_id": hit.metadata.get("brand_id"),
                "country_scope": hit.metadata.get("country_scope"),
                "channel_scope": hit.metadata.get("channel_scope"),
                "locale": hit.metadata.get("locale"),
                "visibility": hit.metadata.get("visibility"),
                "shareability": hit.metadata.get("shareability"),
                "authority_level": hit.metadata.get("authority_level"),
                "risk_level": hit.metadata.get("risk_level"),
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
    evidence_pack = [_evidence_pack_hit(hit) for hit in serialized_hits]
    return {
        "retrieval": "hybrid_rag_v2",
        "total_matches": retrieval.total,
        "candidate_count": retrieval.candidate_count,
        "query_analysis": retrieval.query_analysis.as_trace(),
        "original_query": original_query or query,
        "retrieval_query": query,
        "query_expansion_terms": query_expansion_terms or [],
        "top_hits": retrieval.top_hits,
        "retrieval_methods": getattr(retrieval, "retrieval_methods", []),
        "no_answer_reason": getattr(retrieval, "no_answer_reason", None),
        "latency_ms": getattr(retrieval, "latency_ms", None),
        "grounding_would_apply": retrieval.grounding_would_apply,
        "grounding_source": retrieval.grounding_source,
        "evidence_pack": evidence_pack,
        "locked_facts": _locked_facts(query=query, hits=serialized_hits, entity_terms=retrieval.query_analysis.entity_terms),
        "hits": serialized_hits,
    }


def _evidence_pack_hit(hit: dict[str, Any]) -> dict[str, Any]:
    metadata = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
    source_metadata = hit.get("source_metadata") if isinstance(hit.get("source_metadata"), dict) else {}
    citation = metadata.get("citation") or source_metadata.get("citation") or {}
    source_version = hit.get("published_version") or source_metadata.get("published_version")
    return {
        "item_key": hit.get("item_key"),
        "title": hit.get("title"),
        "source_version": source_version,
        "published_version": hit.get("published_version"),
        "chunk_index": hit.get("chunk_index"),
        "score": hit.get("score"),
        "retrieval_method": hit.get("retrieval_method"),
        "matched_terms": hit.get("matched_terms") or [],
        "score_breakdown": hit.get("score_breakdown") or {},
        "citation": citation,
        "source_metadata": {
            "source_type": metadata.get("source_type") or source_metadata.get("source_type"),
            "file_name": metadata.get("file_name") or source_metadata.get("file_name"),
            "tenant_id": metadata.get("tenant_id") or source_metadata.get("tenant_id"),
            "brand_id": metadata.get("brand_id") or source_metadata.get("brand_id"),
            "country_scope": metadata.get("country_scope") or source_metadata.get("country_scope"),
            "channel_scope": metadata.get("channel_scope") or source_metadata.get("channel_scope"),
            "visibility": metadata.get("visibility") or source_metadata.get("visibility"),
            "shareability": metadata.get("shareability") or source_metadata.get("shareability"),
            "authority_level": metadata.get("authority_level") or source_metadata.get("authority_level"),
            "risk_level": metadata.get("risk_level") or source_metadata.get("risk_level"),
            "market_id": metadata.get("market_id") or source_metadata.get("market_id"),
            "channel": metadata.get("channel") or source_metadata.get("channel"),
            "audience_scope": metadata.get("audience_scope") or source_metadata.get("audience_scope"),
            "language": metadata.get("language") or source_metadata.get("language"),
        },
    }


def _locked_facts(*, query: str, hits: list[dict[str, Any]], entity_terms: list[str]) -> list[dict[str, Any]]:
    if _looks_like_tracking_query(query):
        return []
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


def _looks_like_tracking_query(query: str | None) -> bool:
    text = str(query or "").lower()
    return any(
        marker in text
        for marker in (
            "track",
            "tracking",
            "parcel",
            "package",
            "shipment",
            "waybill",
            "where is",
            "物流",
            "运单",
            "单号",
            "查件",
            "包裹",
            "快递",
        )
    )


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
