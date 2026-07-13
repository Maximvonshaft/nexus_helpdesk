from __future__ import annotations

import json
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


def _rule(*, canary_percent=100, kill_switch=False) -> dict:
    return {
        "primary_provider": "private_ai_runtime",
        "fallback_providers": [],
        "output_contract": "nexus_webchat_runtime_reply_v1",
        "timeout_ms": 3000,
        "kill_switch": kill_switch,
        "canary_percent": canary_percent,
    }


def _mock_db(rule: dict):
    db = Mock()
    query_result = Mock()
    query_result.mappings.return_value.first.return_value = rule
    audit_params: list[dict] = []

    def execute(statement, params=None, *args, **kwargs):
        if "insert into provider_runtime_audit_logs" in str(statement).lower():
            audit_params.append(dict(params or {}))
            return Mock()
        return query_result

    db.execute.side_effect = execute
    db.audit_params = audit_params
    return db


def _last_audit(db) -> tuple[dict, dict]:
    params = db.audit_params[-1]
    return params, json.loads(params["safe_summary"])


@pytest.mark.asyncio
async def test_zero_percent_never_calls_candidate_provider():
    db = _mock_db(_rule(canary_percent=0))
    adapter = _RecordingAdapter()
    ProviderRegistry.register("private_ai_runtime", lambda _db: adapter)

    result = await ProviderRuntimeRouter(db).route(_request())

    assert adapter.calls == 0
    assert result.ok is False
    assert result.error_code == "provider_runtime_control_path"
    audit, safe_summary = _last_audit(db)
    assert audit["status"] == "skipped"
    assert safe_summary["traffic_selection"]["reason"] == "canary_percent_zero"
    assert safe_summary["traffic_selection"]["fallback_result"] == "candidate_not_selected"


@pytest.mark.asyncio
async def test_unsupported_persisted_stage_fails_closed_without_candidate_call():
    db = _mock_db(_rule(canary_percent=2, kill_switch=0))
    adapter = _RecordingAdapter()
    ProviderRegistry.register("private_ai_runtime", lambda _db: adapter)

    result = await ProviderRuntimeRouter(db).route(_request())

    assert adapter.calls == 0
    assert result.error_code == "provider_runtime_traffic_configuration_invalid"
    _, safe_summary = _last_audit(db)
    assert safe_summary["traffic_selection"]["configuration_errors"] == [
        "provider_runtime_canary_percent_invalid"
    ]


@pytest.mark.asyncio
async def test_sqlite_kill_switch_overrides_canary_and_suppresses_candidate():
    db = _mock_db(_rule(canary_percent=100, kill_switch=1))
    adapter = _RecordingAdapter()
    ProviderRegistry.register("private_ai_runtime", lambda _db: adapter)

    result = await ProviderRuntimeRouter(db).route(_request())

    assert adapter.calls == 0
    assert result.error_code == "kill_switch_active"
    audit, safe_summary = _last_audit(db)
    assert audit["status"] == "skipped"
    assert safe_summary["traffic_selection"]["path"] == "kill_switch"
    assert safe_summary["traffic_selection"]["fallback_result"] == "suppressed_by_kill_switch"


@pytest.mark.asyncio
async def test_shadow_executes_candidate_but_discards_customer_output(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "shadow")
    db = _mock_db(_rule(canary_percent=100))
    adapter = _RecordingAdapter()
    ProviderRegistry.register("private_ai_runtime", lambda _db: adapter)

    result = await ProviderRuntimeRouter(db).route(_request())

    assert adapter.calls == 1
    assert result.ok is False
    assert result.structured_output is None
    assert result.error_code == "provider_runtime_shadow_only"
    audit, safe_summary = _last_audit(db)
    assert audit["status"] == "shadow_ok"
    assert safe_summary["traffic_selection"]["path"] == "shadow_only"
    assert safe_summary["traffic_selection"]["authoritative"] is False
    assert safe_summary["traffic_selection"]["fallback_result"] == "shadow_output_discarded"


@pytest.mark.asyncio
async def test_authoritative_success_records_bounded_selection_evidence():
    db = _mock_db(_rule(canary_percent=100))
    adapter = _RecordingAdapter()
    ProviderRegistry.register("private_ai_runtime", lambda _db: adapter)

    result = await ProviderRuntimeRouter(db).route(_request())

    assert result.ok is True
    assert adapter.calls == 1
    audit, safe_summary = _last_audit(db)
    assert audit["status"] == "ok"
    traffic = safe_summary["traffic_selection"]
    assert traffic["schema_version"] == "nexus.provider_runtime.traffic_selection.v1"
    assert traffic["path"] == "canary_authoritative"
    assert traffic["authoritative"] is True
    assert traffic["fallback_result"] == "primary_succeeded"
    assert "customer_reply" not in safe_summary


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
