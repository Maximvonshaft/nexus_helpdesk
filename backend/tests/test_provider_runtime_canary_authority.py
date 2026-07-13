from __future__ import annotations

from unittest.mock import Mock

import pytest

from app.services import provider_runtime as provider_runtime_module
from app.services.provider_runtime.registry import ProviderAdapter, ProviderRegistry
from app.services.provider_runtime.router import ProviderRuntimeRouter
from app.services.provider_runtime.schemas import ProviderRequest, ProviderResult
from app.services.webcall_ai_production.providers.provider_runtime_llm import _route_request


class _RecordingAdapter(ProviderAdapter):
    name = "private_ai_runtime"

    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, db, request):
        self.calls += 1
        return ProviderResult(
            ok=True,
            provider=self.name,
            elapsed_ms=7,
            structured_output={
                "customer_reply": "hello",
                "language": "en",
                "intent": "greeting",
                "handoff_required": False,
                "ticket_should_create": False,
            },
        )


@pytest.fixture(autouse=True)
def _isolated_provider_registry(monkeypatch):
    monkeypatch.setattr(provider_runtime_module, "_BOOTSTRAPPED", True)
    monkeypatch.setattr(ProviderRegistry, "_factories", {})
    for name in (
        "PROVIDER_RUNTIME_PRIMARY_PROVIDER",
        "PROVIDER_RUNTIME_FALLBACK_PROVIDERS",
        "PROVIDER_RUNTIME_OUTPUT_CONTRACT",
        "PROVIDER_RUNTIME_TIMEOUT_MS",
        "PROVIDER_RUNTIME_CANARY_PERCENT",
        "PROVIDER_RUNTIME_KILL_SWITCH",
        "PROVIDER_RUNTIME_TRAFFIC_MODE",
        "WEBCALL_AI_PROVIDER_RUNTIME_PROVIDER",
    ):
        monkeypatch.delenv(name, raising=False)
    yield


def _request(*, scenario: str = "webchat_runtime_reply") -> ProviderRequest:
    return ProviderRequest(
        request_id="req-canary-authority",
        tenant_id="tenant-1",
        tenant_key="tenant-key-1",
        channel_key="website",
        session_id="session-1",
        scenario=scenario,
        body="hello",
        output_contract="nexus_webchat_runtime_reply_v1",
        timeout_ms=1000,
    )


def _mock_db(rule: dict):
    db = Mock()
    query_result = Mock()
    query_result.mappings.return_value.first.return_value = rule

    def execute(statement, params=None, *args, **kwargs):
        if "insert into provider_runtime_audit_logs" in str(statement).lower():
            return Mock()
        return query_result

    db.execute.side_effect = execute
    return db


@pytest.mark.asyncio
async def test_zero_percent_never_calls_candidate_provider():
    db = _mock_db(
        {
            "primary_provider": "private_ai_runtime",
            "fallback_providers": [],
            "output_contract": "nexus_webchat_runtime_reply_v1",
            "timeout_ms": 3000,
            "kill_switch": False,
            "canary_percent": 0,
        }
    )
    adapter = _RecordingAdapter()
    ProviderRegistry.register("private_ai_runtime", lambda _db: adapter)

    result = await ProviderRuntimeRouter(db).route(_request())

    assert adapter.calls == 0
    assert result.ok is False
    assert result.error_code == "provider_runtime_control_path"


@pytest.mark.asyncio
async def test_webcall_private_runtime_alias_cannot_bypass_router(monkeypatch):
    db = _mock_db({})
    adapter = _RecordingAdapter()
    ProviderRegistry.register("private_ai_runtime", lambda _db: adapter)
    routed = ProviderResult.unavailable("router", "provider_runtime_control_path", 0)
    router_calls: list[ProviderRequest] = []

    async def route(self, request):
        router_calls.append(request)
        return routed

    monkeypatch.setenv("WEBCALL_AI_PROVIDER_RUNTIME_PROVIDER", "private_ai_runtime")
    monkeypatch.setattr(ProviderRuntimeRouter, "route", route)

    result = await _route_request(db, _request(scenario="webcall_ai_decision"))

    assert result is routed
    assert len(router_calls) == 1
    assert adapter.calls == 0
