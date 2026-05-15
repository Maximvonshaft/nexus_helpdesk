from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

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
        # Internal handoff guidance belongs in the ticket snapshot only, not the
        # browser response. The customer-visible reply remains AI-generated.
        payload.pop("recommended_agent_action", None)
        return payload


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
) -> WebchatFastReplyResult:
    """Generate one AI-only WebChat reply through the configured provider.

    This function must remain DB-free. Ticket creation, message persistence,
    old AI turns, and polling are deliberately outside this path.

    Phase 1 keeps openclaw_responses as the default provider while adding a
    provider router for codex_auth/openai_responses compatibility work.
    """

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

    provider_result = await generate_fast_reply(
        request=FastAIProviderRequest(
            tenant_key=tenant_key,
            channel_key=channel_key,
            session_id=session_id,
            body=body,
            recent_context=recent_context,
            request_id=request_id,
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
