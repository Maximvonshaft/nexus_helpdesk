from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from . import persona_service
from .knowledge_retrieval_service import KnowledgeChunkHit, search_published_chunks

MAX_PERSONA_SUMMARY_CHARS = 1200
MAX_PERSONA_JSON_CHARS = 1600
MAX_KNOWLEDGE_CHARS = 800
MAX_CONTEXT_HITS = 5

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
    hits, total = search_published_chunks(
        db,
        q=body,
        market_id=market_id,
        channel=channel_key,
        audience_scope=audience_scope,
        limit=MAX_CONTEXT_HITS,
    )
    return sanitize_runtime_context({
        "context_version": "nexus_webchat_runtime_context_v1",
        "tenant_key": tenant_key,
        "metadata_filters": {
            "market_id": market_id,
            "channel": channel_key,
            "language": language,
            "audience_scope": audience_scope,
        },
        "persona_context": _persona_context(profile, match_rank),
        "knowledge_context": _knowledge_context(hits, total),
        "safety_policy": {
            "knowledge_scope": "policy_sop_faq_only",
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
    return {
        "profile_key": profile.profile_key,
        "name": profile.name,
        "summary": _clip(profile.published_summary, MAX_PERSONA_SUMMARY_CHARS),
        "content_json": _clip_json(profile.published_content_json or {}, MAX_PERSONA_JSON_CHARS),
        "published_version": profile.published_version,
        "match_rank": match_rank,
    }


def _knowledge_context(hits: list[KnowledgeChunkHit], total: int) -> dict[str, Any]:
    return {
        "retrieval": "keyword_metadata_filter_v1",
        "total_matches": total,
        "hits": [
            {
                "item_key": hit.item_key,
                "title": hit.title,
                "published_version": hit.published_version,
                "chunk_index": hit.chunk_index,
                "score": hit.score,
                "text": _clip(hit.text, MAX_KNOWLEDGE_CHARS),
                "metadata": {
                    "source_type": hit.metadata.get("source_type"),
                    "file_name": hit.metadata.get("file_name"),
                    "market_id": hit.metadata.get("market_id"),
                    "channel": hit.metadata.get("channel"),
                    "audience_scope": hit.metadata.get("audience_scope"),
                },
            }
            for hit in hits
        ],
    }


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
