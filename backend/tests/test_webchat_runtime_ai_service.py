from __future__ import annotations

import pytest

from app.services.ai_runtime.schemas import RuntimeAIProviderResult
import app.services.webchat_runtime_ai_service as runtime_service


@pytest.mark.asyncio
async def test_runtime_routes_request_through_generic_agent(monkeypatch) -> None:
    monkeypatch.setattr(runtime_service.get_webchat_runtime_settings(), "enabled", True)
    captured = {}

    async def run_agent(request):
        captured["request"] = request
        return RuntimeAIProviderResult(
            ok=True,
            ai_generated=True,
            reply_source="private_ai_runtime",
            raw_provider="private_ai_runtime",
            raw_payload_safe_summary={"agent_runtime": True, "round_count": 1},
            reply="Hello, how can I help?",
            intent="general_support",
            handoff_required=False,
            handoff_reason=None,
            recommended_agent_action=None,
            tool_calls=[],
            elapsed_ms=12,
        )

    monkeypatch.setattr(runtime_service, "run_agent", run_agent)

    result = await runtime_service.generate_webchat_runtime_reply(
        tenant_key="tenant",
        channel_key="website",
        session_id="session",
        body="hello",
        recent_context=[],
        request_id="request",
        language="en",
        runtime_context={"agent_allowed_tools": ["knowledge.search"]},
    )

    assert result.ok is True
    assert result.reply == "Hello, how can I help?"
    assert result.runtime_trace["agent_runtime"] is True
    assert captured["request"].metadata["agent_allowed_tools"] == ["knowledge.search"]
    assert not hasattr(captured["request"], "tracking_fact_summary")


@pytest.mark.asyncio
async def test_runtime_returns_customer_visible_fallback_when_agent_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(runtime_service.get_webchat_runtime_settings(), "enabled", True)

    async def run_agent(request):
        del request
        return RuntimeAIProviderResult(
            ok=True,
            ai_generated=False,
            reply_source="agent_runtime:fallback",
            raw_provider="agent_runtime",
            raw_payload_safe_summary={"agent_runtime": True, "error_code": "all_providers_failed"},
            reply="抱歉，我暂时无法完成这次处理。请稍后重试，或者告诉我是否需要转人工客服。",
            intent="runtime_unavailable",
            handoff_required=False,
            handoff_reason=None,
            recommended_agent_action=None,
            tool_calls=[],
            elapsed_ms=3,
            error_code="all_providers_failed",
            retry_after_ms=1500,
        )

    monkeypatch.setattr(runtime_service, "run_agent", run_agent)

    result = await runtime_service.generate_webchat_runtime_reply(
        tenant_key="tenant",
        channel_key="website",
        session_id="session",
        body="你好",
        recent_context=[],
        language="zh",
    )

    assert result.ok is True
    assert result.ai_generated is False
    assert result.reply
    assert result.error_code == "all_providers_failed"


@pytest.mark.asyncio
async def test_disabled_runtime_still_produces_visible_terminal_reply(monkeypatch) -> None:
    monkeypatch.setattr(runtime_service.get_webchat_runtime_settings(), "enabled", False)

    result = await runtime_service.generate_webchat_runtime_reply(
        tenant_key="tenant",
        channel_key="website",
        session_id="session",
        body="hello",
        recent_context=[],
        language="en",
    )

    assert result.ok is True
    assert result.reply
    assert result.reply_source == "agent_runtime:fallback"
    assert result.error_code == "ai_unavailable"
