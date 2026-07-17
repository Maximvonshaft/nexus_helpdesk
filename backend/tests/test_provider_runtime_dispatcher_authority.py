from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.ai_runtime.schemas import RuntimeAIProviderRequest
from app.services.provider_runtime.schemas import ProviderResult
import app.services.provider_runtime.webchat_runtime_dispatcher as dispatcher


class _DummySession:
    def close(self) -> None:
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_code,path",
    [
        ("provider_canary_control_path", "control"),
        ("provider_shadow_only", "shadow_only"),
        ("kill_switch_active", "kill_switch"),
    ],
)
async def test_non_authoritative_traffic_cannot_create_reply_or_action_authority(
    monkeypatch,
    error_code: str,
    path: str,
):
    monkeypatch.setattr(dispatcher, "SessionLocal", lambda: _DummySession())
    monkeypatch.setattr(
        dispatcher,
        "build_webchat_runtime_context",
        lambda *args, **kwargs: {
            "context_version": "nexus.webchat_runtime_context",
            "knowledge_context": {
                "retrieval": "unavailable",
                "locked_facts": [],
                "hits": [],
            },
        },
    )
    route = AsyncMock(
        return_value=ProviderResult(
            ok=False,
            provider="router",
            elapsed_ms=7,
            structured_output=None,
            raw_payload_safe_summary={
                "traffic": {
                    "path": path,
                    "authoritative": False,
                    "execute_candidate": path == "shadow_only",
                }
            },
            error_code=error_code,
            fallback_allowed=False,
        )
    )
    monkeypatch.setattr(dispatcher.ProviderRuntimeRouter, "route", route)

    result = await dispatcher.dispatch_webchat_runtime_reply(
        request=RuntimeAIProviderRequest(
            tenant_key="tenant-1",
            channel_key="webchat",
            session_id="session-1",
            request_id="request-1",
            body="hello",
        )
    )

    assert result.ok is False
    assert result.ai_generated is False
    assert result.reply is None
    assert result.intent is None
    assert result.handoff_required is False
    assert result.recommended_agent_action is None
    assert result.tool_intents == []
    assert result.error_code == error_code
    route.assert_awaited_once()
