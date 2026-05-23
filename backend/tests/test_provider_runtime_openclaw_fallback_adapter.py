from __future__ import annotations

from unittest.mock import Mock

import pytest

from app.services.ai_runtime.schemas import FastAIProviderResult
from app.services.provider_runtime.adapters.openclaw_responses import OpenClawResponsesAdapter
from app.services.provider_runtime.schemas import ProviderRequest


def _provider_request() -> ProviderRequest:
    return ProviderRequest(
        request_id="req1",
        tenant_id="default",
        tenant_key="default",
        channel_key="website",
        session_id="sess1",
        scenario="webchat_fast_reply",
        body="hello",
        recent_context=[{"role": "customer", "text": "hello"}],
        output_contract="speedaf_webchat_fast_reply_v1",
        timeout_ms=10000,
    )


@pytest.mark.asyncio
async def test_openclaw_responses_adapter_returns_provider_runtime_output(monkeypatch):
    async def fake_generate(self, request):
        return FastAIProviderResult(
            ok=True,
            ai_generated=True,
            reply_source="openclaw_responses",
            raw_provider="openclaw_responses",
            raw_payload_safe_summary={"parsed": True},
            reply="I can help with that.",
            intent="other",
            tracking_number=None,
            handoff_required=False,
            handoff_reason=None,
            recommended_agent_action=None,
            elapsed_ms=12,
        )

    monkeypatch.setattr(
        "app.services.provider_runtime.adapters.openclaw_responses.OpenClawResponsesProvider.generate",
        fake_generate,
    )

    result = await OpenClawResponsesAdapter().generate(Mock(), _provider_request())

    assert result.ok is True
    assert result.provider == "openclaw_responses"
    assert result.structured_output == {
        "reply": "I can help with that.",
        "intent": "other",
        "tracking_number": None,
        "handoff_required": False,
        "handoff_reason": None,
        "recommended_agent_action": None,
    }
    assert "access_token" not in str(result.raw_payload_safe_summary)


@pytest.mark.asyncio
async def test_openclaw_responses_adapter_unavailable_allows_fallback(monkeypatch):
    async def fake_generate(self, request):
        return FastAIProviderResult.unavailable(provider="openclaw_responses", error_code="ai_unavailable", elapsed_ms=7)

    monkeypatch.setattr(
        "app.services.provider_runtime.adapters.openclaw_responses.OpenClawResponsesProvider.generate",
        fake_generate,
    )

    result = await OpenClawResponsesAdapter().generate(Mock(), _provider_request())

    assert result.ok is False
    assert result.error_code == "ai_unavailable"
    assert result.fallback_allowed is True
