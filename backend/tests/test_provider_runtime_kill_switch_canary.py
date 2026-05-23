from __future__ import annotations

from unittest.mock import Mock

import pytest

from app.services.provider_runtime.registry import ProviderAdapter, ProviderRegistry
from app.services.provider_runtime.router import ProviderRuntimeRouter
from app.services.provider_runtime.schemas import ProviderRequest, ProviderResult


class SuccessAdapter(ProviderAdapter):
    def __init__(self, name: str):
        self.name = name

    async def generate(self, db, request):
        return ProviderResult(
            ok=True,
            provider=self.name,
            elapsed_ms=5,
            structured_output={
                "reply": "I can help with that.",
                "intent": "other",
                "tracking_number": None,
                "handoff_required": False,
                "handoff_reason": None,
                "recommended_agent_action": None,
            },
        )


def _db_for_rule(rule: dict | None):
    mock_db = Mock()
    select_result = Mock()
    select_result.mappings.return_value.first.return_value = rule

    def execute(stmt, params=None, *args, **kwargs):
        if "INSERT INTO provider_runtime_audit_logs" in str(stmt):
            return Mock()
        return select_result

    mock_db.execute.side_effect = execute
    return mock_db


def _request() -> ProviderRequest:
    return ProviderRequest(
        request_id="req1",
        tenant_id="default",
        tenant_key="default",
        channel_key="website",
        session_id="sess1",
        scenario="webchat_fast_reply",
        body="hello",
        output_contract="speedaf_webchat_fast_reply_v1",
        timeout_ms=10000,
    )


@pytest.mark.asyncio
async def test_canary_zero_routes_to_openclaw_without_codex(monkeypatch):
    import app.services.provider_runtime as provider_runtime_module

    monkeypatch.setattr(provider_runtime_module, "bootstrap_provider_runtime", lambda: None)
    ProviderRegistry.register("codex_app_server", lambda db: SuccessAdapter("codex_app_server"))
    ProviderRegistry.register("openclaw_responses", lambda db: SuccessAdapter("openclaw_responses"))
    db = _db_for_rule({
        "primary_provider": "codex_app_server",
        "fallback_providers": ["openclaw_responses", "rule_engine"],
        "output_contract": "speedaf_webchat_fast_reply_v1",
        "timeout_ms": 10000,
        "kill_switch": False,
        "canary_percent": 0,
    })

    result = await ProviderRuntimeRouter(db).route(_request())

    assert result.ok is True
    assert result.provider == "openclaw_responses"


@pytest.mark.asyncio
async def test_canary_full_routes_to_codex(monkeypatch):
    import app.services.provider_runtime as provider_runtime_module

    monkeypatch.setattr(provider_runtime_module, "bootstrap_provider_runtime", lambda: None)
    ProviderRegistry.register("codex_app_server", lambda db: SuccessAdapter("codex_app_server"))
    ProviderRegistry.register("openclaw_responses", lambda db: SuccessAdapter("openclaw_responses"))
    db = _db_for_rule({
        "primary_provider": "codex_app_server",
        "fallback_providers": ["openclaw_responses", "rule_engine"],
        "output_contract": "speedaf_webchat_fast_reply_v1",
        "timeout_ms": 10000,
        "kill_switch": False,
        "canary_percent": 100,
    })

    result = await ProviderRuntimeRouter(db).route(_request())

    assert result.ok is True
    assert result.provider == "codex_app_server"


@pytest.mark.asyncio
async def test_kill_switch_routes_to_openclaw(monkeypatch):
    import app.services.provider_runtime as provider_runtime_module

    monkeypatch.setattr(provider_runtime_module, "bootstrap_provider_runtime", lambda: None)
    ProviderRegistry.register("codex_app_server", lambda db: SuccessAdapter("codex_app_server"))
    ProviderRegistry.register("openclaw_responses", lambda db: SuccessAdapter("openclaw_responses"))
    db = _db_for_rule({
        "primary_provider": "codex_app_server",
        "fallback_providers": '["openclaw_responses","rule_engine"]',
        "output_contract": "speedaf_webchat_fast_reply_v1",
        "timeout_ms": 10000,
        "kill_switch": True,
        "canary_percent": 100,
    })

    result = await ProviderRuntimeRouter(db).route(_request())

    assert result.ok is True
    assert result.provider == "openclaw_responses"
