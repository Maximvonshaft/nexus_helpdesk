from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .agent_runtime.service import run_agent
from .ai_runtime.schemas import RuntimeAIProviderRequest
from .customer_language import detect_customer_language
from .webchat_runtime_config import get_webchat_runtime_settings
from .webchat_runtime_metrics import record_webchat_runtime_metric


@dataclass(frozen=True)
class WebchatRuntimeReplyResult:
    ok: bool
    ai_generated: bool
    reply_source: str | None
    reply: str | None
    intent: str | None
    handoff_required: bool
    handoff_reason: str | None
    recommended_agent_action: str | None
    elapsed_ms: int
    error_code: str | None = None
    retry_after_ms: int | None = None
    runtime_trace: dict[str, Any] | None = None
    tool_calls: list[dict[str, Any]] | None = None

    def to_response(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("recommended_agent_action", None)
        return payload


async def generate_webchat_runtime_reply(
    *,
    tenant_key: str,
    channel_key: str,
    session_id: str,
    body: str,
    recent_context: list[dict[str, Any]] | None,
    request_id: str | None = None,
    market_id: int | None = None,
    language: str | None = None,
    runtime_context: dict[str, Any] | None = None,
) -> WebchatRuntimeReplyResult:
    settings = get_webchat_runtime_settings()
    if not settings.enabled:
        result = WebchatRuntimeReplyResult(
            ok=True,
            ai_generated=False,
            reply_source="agent_runtime:fallback",
            reply=_fallback(language, body),
            intent="runtime_disabled",
            handoff_required=False,
            handoff_reason=None,
            recommended_agent_action=None,
            elapsed_ms=0,
            error_code="ai_unavailable",
            retry_after_ms=1500,
            runtime_trace={"agent_runtime": True, "error_code": "ai_unavailable"},
            tool_calls=[],
        )
        record_webchat_runtime_metric(status="ai_unavailable", elapsed_ms=0)
        return result

    language_decision = detect_customer_language(body, explicit=language)
    provider_result = await run_agent(
        RuntimeAIProviderRequest(
            tenant_key=tenant_key,
            channel_key=channel_key,
            session_id=session_id,
            body=body,
            recent_context=recent_context,
            request_id=request_id,
            market_id=market_id,
            language=language_decision.language,
            metadata=runtime_context or {},
        )
    )
    safe_summary = provider_result.raw_payload_safe_summary or {}
    result = WebchatRuntimeReplyResult(
        ok=provider_result.ok,
        ai_generated=provider_result.ai_generated,
        reply_source=provider_result.reply_source,
        reply=provider_result.reply,
        intent=provider_result.intent,
        handoff_required=provider_result.handoff_required,
        handoff_reason=provider_result.handoff_reason,
        recommended_agent_action=provider_result.recommended_agent_action,
        elapsed_ms=provider_result.elapsed_ms,
        error_code=provider_result.error_code,
        retry_after_ms=provider_result.retry_after_ms,
        runtime_trace=safe_summary,
        tool_calls=provider_result.tool_calls,
    )
    record_webchat_runtime_metric(
        status="ok" if result.ok else (result.error_code or "ai_unavailable"),
        intent=result.intent,
        handoff_required=result.handoff_required,
        elapsed_ms=result.elapsed_ms,
    )
    return result


def _fallback(language: str | None, body: str) -> str:
    hint = str(language or "").strip().lower()
    if hint == "zh" or any("\u4e00" <= char <= "\u9fff" for char in body):
        return "抱歉，我暂时无法完成这次处理。请稍后重试，或者告诉我是否需要转人工客服。"
    if hint == "de":
        return "Entschuldigung, ich konnte diese Anfrage gerade nicht abschließen. Bitte versuchen Sie es erneut oder bitten Sie um menschlichen Support."
    return "Sorry, I could not complete that request right now. Please try again or ask for human support."
