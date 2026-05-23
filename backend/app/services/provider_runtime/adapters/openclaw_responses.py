from __future__ import annotations

import time
from typing import Any

from ...ai_runtime.openclaw_responses_provider import OpenClawResponsesProvider
from ...ai_runtime.schemas import FastAIProviderRequest
from ...webchat_fast_config import get_webchat_fast_settings
from ..registry import ProviderAdapter
from ..schemas import ProviderCapabilities, ProviderRequest, ProviderResult


class OpenClawResponsesAdapter(ProviderAdapter):
    name = "openclaw_responses"
    capabilities = ProviderCapabilities(
        fast_reply=True,
        structured_output=True,
        handoff_decision=True,
        supports_tracking_context=True,
        safety_level="reply_only",
    )

    async def generate(self, db, request: ProviderRequest) -> ProviderResult:
        started = time.monotonic()
        provider = OpenClawResponsesProvider(get_webchat_fast_settings())
        result = await provider.generate(
            FastAIProviderRequest(
                tenant_key=request.tenant_key,
                channel_key=request.channel_key,
                session_id=request.session_id,
                body=str(request.body or ""),
                recent_context=_clean_recent_context(request.recent_context),
                request_id=request.request_id,
                tracking_fact_summary=request.tracking_fact_summary,
                tracking_fact_metadata=(request.metadata or {}).get("tracking_fact_metadata"),
                tracking_fact_evidence_present=request.tracking_fact_evidence_present,
            )
        )
        elapsed_ms = result.elapsed_ms or int((time.monotonic() - started) * 1000)
        if not result.ok:
            return ProviderResult.unavailable(
                provider=self.name,
                error_code=result.error_code or "openclaw_unavailable",
                elapsed_ms=elapsed_ms,
                fallback_allowed=True,
            )

        return ProviderResult(
            ok=True,
            provider=self.name,
            elapsed_ms=elapsed_ms,
            raw_payload_safe_summary=result.raw_payload_safe_summary or {"parsed": True},
            structured_output={
                "reply": result.reply,
                "intent": result.intent or "other",
                "tracking_number": result.tracking_number,
                "handoff_required": bool(result.handoff_required),
                "handoff_reason": result.handoff_reason,
                "recommended_agent_action": result.recommended_agent_action,
            },
        )


def _clean_recent_context(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
