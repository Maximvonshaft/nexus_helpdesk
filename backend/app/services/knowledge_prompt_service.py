from __future__ import annotations

import json
from typing import Any

MAX_KNOWLEDGE_PROMPT_CHARS = 3600
MAX_PROMPT_HITS = 5
MAX_HIT_TEXT_CHARS = 650
MAX_DIRECT_ANSWER_CHARS = 1000


def build_knowledge_prompt_block(knowledge_context: dict[str, Any] | None) -> str:
    if not isinstance(knowledge_context, dict):
        return ""
    hits = [hit for hit in knowledge_context.get("hits") or [] if isinstance(hit, dict)]
    if not hits:
        return ""
    ordered = sorted(
        hits[:MAX_PROMPT_HITS],
        key=lambda hit: (0 if hit.get("direct_answer") else 1, -float(hit.get("score") or 0)),
    )
    lines = [
        "Knowledge context (sanitized KB, not live parcel tracking evidence):",
        "- If KB directly answers the customer question, answer from KB and do not say cannot confirm.",
        "- Never treat knowledge documents as live parcel tracking evidence.",
    ]
    for index, hit in enumerate(ordered, start=1):
        source = hit.get("source_metadata") if isinstance(hit.get("source_metadata"), dict) else {}
        metadata = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
        source_metadata = {
            "source_type": metadata.get("source_type") or source.get("source_type"),
            "file_name": metadata.get("file_name") or source.get("file_name"),
            "market_id": metadata.get("market_id") or source.get("market_id"),
            "channel": metadata.get("channel") or source.get("channel"),
            "audience_scope": metadata.get("audience_scope") or source.get("audience_scope"),
            "language": metadata.get("language") or source.get("language"),
            "citation": metadata.get("citation") or source.get("citation"),
        }
        direct_answer = _clip(hit.get("direct_answer"), MAX_DIRECT_ANSWER_CHARS)
        lines.extend(
            [
                f"[KB {index}] item_key={_clean(hit.get('item_key'))} title={_clean(hit.get('title'))} score={float(hit.get('score') or 0):.3f}",
                f"retrieval_method={_clean(hit.get('retrieval_method'))} chunk_index={hit.get('chunk_index')} answer_mode={_clean(hit.get('answer_mode'))}",
                f"matched_terms={_json(hit.get('matched_terms') or [])}",
                f"score_breakdown={_json(hit.get('score_breakdown') or {})}",
                f"source_metadata={_json(source_metadata)}",
            ]
        )
        if direct_answer:
            lines.append(f"direct_answer={direct_answer}")
        lines.append("text=" + _clip(hit.get("text"), MAX_HIT_TEXT_CHARS))
    return _clip("\n".join(lines), MAX_KNOWLEDGE_PROMPT_CHARS)


def summarize_rag_trace(runtime_context: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(runtime_context, dict):
        return {}
    knowledge = runtime_context.get("knowledge_context") if isinstance(runtime_context.get("knowledge_context"), dict) else {}
    return {
        "query_analysis": knowledge.get("query_analysis") or (runtime_context.get("rag_trace") or {}).get("query_analysis"),
        "candidate_count": knowledge.get("candidate_count"),
        "total_matches": knowledge.get("total_matches"),
        "top_hits": knowledge.get("top_hits") or [],
        "injected_knowledge": [
            {
                "item_key": hit.get("item_key"),
                "title": hit.get("title"),
                "score": hit.get("score"),
                "retrieval_method": hit.get("retrieval_method"),
                "matched_terms": hit.get("matched_terms") or [],
            }
            for hit in (knowledge.get("hits") or [])[:MAX_PROMPT_HITS]
            if isinstance(hit, dict)
        ],
        "grounding_would_apply": knowledge.get("grounding_would_apply"),
        "grounding_source": knowledge.get("grounding_source"),
    }


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())[:240]


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))[:700]
    except (TypeError, ValueError):
        return "{}"
