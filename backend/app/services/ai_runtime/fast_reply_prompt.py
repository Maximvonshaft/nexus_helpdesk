from __future__ import annotations

import hashlib
from typing import Any

from ..knowledge_prompt_service import build_knowledge_prompt_block
from ..webchat_ai_decision_runtime.prompt_builder import build_ai_decision_instructions


def _clip(value: str | None, limit: int) -> str:
    cleaned = (value or "").strip()
    return cleaned[:limit]


def _context_block(recent_context: list[dict[str, str]]) -> str:
    if not recent_context:
        return "(none)"
    lines = []
    for item in recent_context:
        speaker = "Customer" if item["role"] == "customer" else "AI"
        lines.append(f"{speaker}: {item['text']}")
    return "\n".join(lines)


def build_fast_reply_instructions() -> str:
    return build_ai_decision_instructions()


def _trusted_fact_block(*, tracking_fact_summary: str | None, tracking_fact_evidence_present: bool) -> str:
    if not tracking_fact_evidence_present:
        return ""
    summary = _clip(tracking_fact_summary, 1600)
    if not summary:
        return ""
    return "Trusted tracking fact block:\n" + summary + "\n\n"


def build_fast_reply_input_text(
    *,
    body: str,
    recent_context: list[dict[str, str]],
    max_prompt_chars: int,
    tracking_fact_summary: str | None = None,
    tracking_fact_evidence_present: bool = False,
    knowledge_context: dict[str, Any] | None = None,
) -> str:
    fact_block = _trusted_fact_block(
        tracking_fact_summary=tracking_fact_summary,
        tracking_fact_evidence_present=tracking_fact_evidence_present,
    )
    text = (
        "Recent conversation:\n"
        f"{_context_block(recent_context)}\n\n"
        f"{fact_block}"
        f"{build_knowledge_prompt_block(knowledge_context) + chr(10) + chr(10) if knowledge_context else ''}"
        "Customer message:\n"
        f"{_clip(body, 2000)}"
    )
    return text[:max_prompt_chars]


def build_fast_reply_session_key(*, tenant_key: str, session_id: str) -> str:
    raw = f"webchat-fast:{tenant_key or 'default'}:{session_id}"
    if len(raw) <= 180:
        return raw
    digest = hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:32]
    return f"webchat-fast:{digest}"
