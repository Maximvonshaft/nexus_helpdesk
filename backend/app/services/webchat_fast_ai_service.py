from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .ai_runtime.openclaw_responses_provider import (
    build_fast_reply_input_text,
    build_fast_reply_instructions,
    build_fast_reply_session_key,
)
from .ai_runtime.provider_router import generate_fast_reply
from .ai_runtime.schemas import FastAIProviderRequest, FastAIProviderResult
from .webchat_fast_config import get_webchat_fast_settings
from .webchat_fast_reply_metrics import record_fast_reply_metric


@dataclass(frozen=True)
class WebchatFastReplyResult:
    ok: bool
    ai_generated: bool
    reply_source: str | None
    reply: str | None
    intent: str | None
    tracking_number: str | None
    handoff_required: bool
    handoff_reason: str | None
    recommended_agent_action: str | None
    ticket_creation_queued: bool
    elapsed_ms: int
    error_code: str | None = None
    retry_after_ms: int | None = None

    def to_response(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("recommended_agent_action", None)
        return payload


def _clip(value: str | None, limit: int) -> str:
    cleaned = (value or "").strip()
    return cleaned[:limit]


def _clean_context(recent_context: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    settings = get_webchat_fast_settings()
    items = recent_context or []
    cleaned: list[dict[str, str]] = []
    for item in items[-settings.history_turns * 2:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in {"customer", "visitor", "user", "ai", "assistant", "agent"}:
            continue
        normalized_role = "customer" if role in {"customer", "visitor", "user"} else "ai"
        text = _clip(str(item.get("text") or item.get("body") or ""), 500)
        if text:
            cleaned.append({"role": normalized_role, "text": text})
    return cleaned[-settings.history_turns * 2:]


def _instructions() -> str:
    return build_fast_reply_instructions()


def _input_text(
    *,
    body: str,
    recent_context: list[dict[str, str]],
    tracking_fact_summary: str | None = None,
    tracking_fact_evidence_present: bool = False,
) -> str:
    settings = get_webchat_fast_settings()
    return build_fast_reply_input_text(
        body=body,
        recent_context=recent_context,
        max_prompt_chars=settings.max_prompt_chars,
        tracking_fact_summary=tracking_fact_summary,
        tracking_fact_evidence_present=tracking_fact_evidence_present,
    )


def _session_key(*, tenant_key: str, session_id: str) -> str:
    return build_fast_reply_session_key(tenant_key=tenant_key, session_id=session_id)


def _result_from_provider(provider_result: FastAIProviderResult) -> WebchatFastReplyResult:
    return WebchatFastReplyResult(
        ok=provider_result.ok,
        ai_generated=provider_result.ai_generated,
        reply_source=provider_result.reply_source,
        reply=provider_result.reply,
        intent=provider_result.intent,
        tracking_number=provider_result.tracking_number,
        handoff_required=provider_result.handoff_required,
        handoff_reason=provider_result.handoff_reason,
        recommended_agent_action=provider_result.recommended_agent_action,
        ticket_creation_queued=False,
        elapsed_ms=provider_result.elapsed_ms,
        error_code=provider_result.error_code,
        retry_after_ms=provider_result.retry_after_ms,
    )


async def generate_webchat_fast_reply(
    *,
    tenant_key: str,
    channel_key: str,
    session_id: str,
    body: str,
    recent_context: list[dict[str, Any]] | None,
    request_id: str | None = None,
    tracking_fact_summary: str | None = None,
    tracking_fact_metadata: dict[str, Any] | None = None,
    tracking_fact_evidence_present: bool = False,
) -> WebchatFastReplyResult:
    settings = get_webchat_fast_settings()
    if not settings.enabled:
        result = WebchatFastReplyResult(
            ok=False,
            ai_generated=False,
            reply_source=None,
            reply=None,
            intent=None,
            tracking_number=None,
            handoff_required=False,
            handoff_reason=None,
            recommended_agent_action=None,
            ticket_creation_queued=False,
            elapsed_ms=0,
            error_code="ai_unavailable",
            retry_after_ms=1500,
        )
        record_fast_reply_metric(status="ai_unavailable", elapsed_ms=0)
        return result

    evidence_present = bool(tracking_fact_evidence_present and tracking_fact_summary)
    provider_result = await generate_fast_reply(
        request=FastAIProviderRequest(
            tenant_key=tenant_key,
            channel_key=channel_key,
            session_id=session_id,
            body=body,
            recent_context=recent_context,
            request_id=request_id,
            tracking_fact_summary=tracking_fact_summary if evidence_present else None,
            tracking_fact_metadata=tracking_fact_metadata if evidence_present else None,
            tracking_fact_evidence_present=evidence_present,
        ),
        settings=settings,
    )

    status = "ok" if provider_result.ok else (provider_result.error_code or "ai_unavailable")
    record_fast_reply_metric(
        status=status,
        intent=provider_result.intent,
        handoff_required=provider_result.handoff_required,
        elapsed_ms=provider_result.elapsed_ms,
    )
    return _result_from_provider(provider_result)
