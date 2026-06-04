from __future__ import annotations

import hashlib
import time
from typing import Any

from ..webchat_ai_decision_runtime.prompt_builder import build_ai_decision_instructions
from ..webchat_fast_output_parser import (
    FastReplyParseError,
    ParsedFastReply,
    UnexpectedToolCallError,
    parse_openclaw_fast_reply,
)
from ..webchat_fast_reply_metrics import record_openclaw_responses_metric
from ..webchat_openclaw_responses_client import OpenClawResponsesError, call_openclaw_responses
from .provider_base import BaseFastAIProvider
from .schemas import FastAIProviderRequest, FastAIProviderResult
from ..knowledge_prompt_service import build_knowledge_prompt_block


def _clip(value: str | None, limit: int) -> str:
    cleaned = (value or "").strip()
    return cleaned[:limit]


def _clean_context(recent_context: list[dict[str, Any]] | None, *, history_turns: int) -> list[dict[str, str]]:
    items = recent_context or []
    cleaned: list[dict[str, str]] = []
    for item in items[-history_turns * 2:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in {"customer", "visitor", "user", "ai", "assistant", "agent"}:
            continue
        normalized_role = "customer" if role in {"customer", "visitor", "user"} else "ai"
        text = _clip(str(item.get("text") or item.get("body") or ""), 500)
        if text:
            cleaned.append({"role": normalized_role, "text": text})
    return cleaned[-history_turns * 2:]


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


def _success_from_parsed(parsed: ParsedFastReply, *, elapsed_ms: int, tracking_fact_metadata: dict[str, Any] | None = None) -> FastAIProviderResult:
    safe_summary: dict[str, Any] = {"parsed": True}
    if parsed.ai_decision:
        safe_summary["ai_decision"] = parsed.ai_decision
    else:
        safe_summary["ai_decision"] = {
            "customer_reply": parsed.reply,
            "intent": parsed.intent,
            "confidence": parsed.confidence,
            "risk_level": parsed.risk_level,
            "next_action": parsed.next_action,
            "handoff_required": parsed.handoff_required,
            "handoff_reason": parsed.handoff_reason,
            "tool_calls": parsed.tool_calls,
            "evidence_used": parsed.evidence_used,
            "safety_notes": parsed.safety_notes,
        }
    if tracking_fact_metadata:
        safe_summary["tracking_fact"] = tracking_fact_metadata
    return FastAIProviderResult(
        ok=True,
        ai_generated=True,
        reply_source="openclaw_responses",
        raw_provider="openclaw_responses",
        raw_payload_safe_summary=safe_summary,
        reply=parsed.reply,
        intent=parsed.intent,
        tracking_number=parsed.tracking_number,
        handoff_required=parsed.handoff_required,
        handoff_reason=parsed.handoff_reason,
        recommended_agent_action=parsed.recommended_agent_action,
        tool_intents=[],
        elapsed_ms=elapsed_ms,
    )


class OpenClawResponsesProvider(BaseFastAIProvider):
    name = "openclaw_responses"

    def is_configured(self) -> bool:
        return bool(self.settings.enabled and self.settings.is_openclaw_configured)

    async def generate(self, request: FastAIProviderRequest) -> FastAIProviderResult:
        started = time.monotonic()
        if not self.is_configured():
            return FastAIProviderResult.unavailable(provider=self.name, error_code="ai_unavailable", elapsed_ms=0)

        normalized_body = _clip(request.body, 2000)
        context = _clean_context(request.recent_context, history_turns=self.settings.history_turns)
        try:
            response = await call_openclaw_responses(
                session_key=build_fast_reply_session_key(tenant_key=request.tenant_key, session_id=request.session_id),
                instructions=build_fast_reply_instructions(),
                input_text=build_fast_reply_input_text(
                    body=normalized_body,
                    recent_context=context,
                    max_prompt_chars=self.settings.max_prompt_chars,
                    tracking_fact_summary=request.tracking_fact_summary,
                    tracking_fact_evidence_present=request.tracking_fact_evidence_present,
                    knowledge_context=(request.metadata or {}).get("knowledge_context") if isinstance(request.metadata, dict) else None,
                ),
                request_id=request.request_id,
                settings=self.settings,
            )
            record_openclaw_responses_metric(
                status="ok",
                agent_id=self.settings.openclaw_responses_agent_id,
                elapsed_ms=response.elapsed_ms,
            )
            parsed = parse_openclaw_fast_reply(response.payload)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            return _success_from_parsed(parsed, elapsed_ms=elapsed_ms, tracking_fact_metadata=request.tracking_fact_metadata)
        except UnexpectedToolCallError:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            return FastAIProviderResult.unavailable(
                provider=self.name,
                error_code="ai_unexpected_tool_call",
                elapsed_ms=elapsed_ms,
            )
        except FastReplyParseError:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            return FastAIProviderResult.unavailable(
                provider=self.name,
                error_code="ai_invalid_output",
                elapsed_ms=elapsed_ms,
            )
        except OpenClawResponsesError:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            record_openclaw_responses_metric(
                status="unavailable",
                agent_id=self.settings.openclaw_responses_agent_id,
                elapsed_ms=elapsed_ms,
            )
            return FastAIProviderResult.unavailable(provider=self.name, error_code="ai_unavailable", elapsed_ms=elapsed_ms)
