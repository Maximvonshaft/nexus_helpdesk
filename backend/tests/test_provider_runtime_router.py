import json
from unittest.mock import Mock

import pytest

from app.services import provider_runtime as provider_runtime_module
from app.services.provider_runtime.registry import ProviderAdapter, ProviderRegistry
from app.services.provider_runtime.router import ProviderRuntimeRouter
from app.services.provider_runtime.schemas import ProviderRequest, ProviderResult


class DummyAdapter(ProviderAdapter):
    def __init__(self, name, result):
        self.name = name
        self._result = result
        self.calls = 0

    async def generate(self, db, req):
        self.calls += 1
        return self._result


@pytest.fixture(autouse=True)
def isolated_provider_registry(monkeypatch):
    monkeypatch.setattr(provider_runtime_module, "_BOOTSTRAPPED", True)
    monkeypatch.setattr(ProviderRegistry, "_factories", {})
    for name in (
        "PROVIDER_RUNTIME_TRAFFIC_MODE",
        "PROVIDER_RUNTIME_CANARY_PERCENT",
        "PROVIDER_RUNTIME_KILL_SWITCH",
    ):
        monkeypatch.delenv(name, raising=False)
    yield


def _mock_db(rule: dict):
    mock_db = Mock()
    mock_rule = Mock()
    mock_rule.mappings.return_value.first.return_value = rule
    audit_rows = []

    def mock_db_execute(stmt, params, *args, **kwargs):
        query = str(stmt).lower()
        if "insert into provider_runtime_audit_logs" in query:
            audit_rows.append(dict(params))
            return Mock()
        return mock_rule

    mock_db.execute.side_effect = mock_db_execute
    mock_db.audit_rows = audit_rows
    return mock_db


def _request() -> ProviderRequest:
    return ProviderRequest(
        request_id="req1",
        tenant_id="t1",
        tenant_key="tk1",
        channel_key="c1",
        session_id="s1",
        scenario="webchat_runtime_reply",
        body="hello",
        output_contract="nexus_webchat_runtime_reply_v1",
        timeout_ms=1000,
    )


def _valid_result() -> ProviderResult:
    return ProviderResult(
        ok=True,
        provider="private_ai_runtime",
        elapsed_ms=100,
        structured_output={
            "customer_reply": "hi",
            "language": "en",
            "intent": "greeting",
            "handoff_required": False,
            "ticket_should_create": False,
        },
    )


def _trusted_tracking_followup_request() -> ProviderRequest:
    return ProviderRequest(
        request_id="req-tracking-followup",
        tenant_id="t1",
        tenant_key="tk1",
        channel_key="c1",
        session_id="s1",
        scenario="webchat_runtime_reply",
        body="The recipient says they did not receive it. What should we do?",
        tracking_fact_summary="Trusted tracking fact: parcel ending 007813 is delivered.",
        tracking_fact_evidence_present=True,
        output_contract="nexus_webchat_runtime_reply_v1",
        timeout_ms=1000,
        metadata={
            "knowledge_context": {
                "locked_facts": [
                    {
                        "item_key": "nexus.support.customer.kb.ch.service.availability",
                        "answer": "Switzerland domestic-to-domestic service is currently unavailable.",
                        "source": {"item_key": "nexus.support.customer.kb.ch.service.availability"},
                    }
                ]
            }
        },
    )


@pytest.mark.asyncio
async def test_provider_runtime_router_single_runtime_success_and_audit():
    mock_db = _mock_db(
        {
            "primary_provider": "private_ai_runtime",
            "fallback_providers": [],
            "output_contract": "nexus_webchat_runtime_reply_v1",
            "timeout_ms": 3000,
            "kill_switch": False,
            "canary_percent": 100,
        }
    )
    adapter = DummyAdapter("private_ai_runtime", _valid_result())
    ProviderRegistry.register("private_ai_runtime", lambda db: adapter)

    result = await ProviderRuntimeRouter(mock_db).route(_request())

    assert result.ok
    assert result.provider == "private_ai_runtime"
    assert result.structured_output["customer_reply"] == "hi"
    assert adapter.calls == 1
    assert mock_db.execute.call_count == 2
    summary = json.loads(mock_db.audit_rows[-1]["safe_summary"])
    assert summary["traffic_selection"]["path"] == "canary_authoritative"
    assert summary["traffic_selection"]["authoritative"] is True


@pytest.mark.asyncio
async def test_zero_percent_routes_to_control_without_calling_candidate():
    mock_db = _mock_db(
        {
            "primary_provider": "private_ai_runtime",
            "fallback_providers": [],
            "output_contract": "nexus_webchat_runtime_reply_v1",
            "timeout_ms": 3000,
            "kill_switch": False,
            "canary_percent": 0,
        }
    )
    adapter = DummyAdapter("private_ai_runtime", _valid_result())
    ProviderRegistry.register("private_ai_runtime", lambda db: adapter)

    result = await ProviderRuntimeRouter(mock_db).route(_request())

    assert not result.ok
    assert result.error_code == "provider_canary_control_path"
    assert adapter.calls == 0
    summary = json.loads(mock_db.audit_rows[-1]["safe_summary"])
    assert summary["traffic_selection"]["path"] == "control"
    assert summary["traffic_selection"]["execute_candidate"] is False


@pytest.mark.asyncio
async def test_environment_zero_percent_overrides_database_rule(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_CANARY_PERCENT", "0")
    mock_db = _mock_db(
        {
            "primary_provider": "private_ai_runtime",
            "fallback_providers": [],
            "output_contract": "nexus_webchat_runtime_reply_v1",
            "timeout_ms": 3000,
            "kill_switch": False,
            "canary_percent": 100,
        }
    )
    adapter = DummyAdapter("private_ai_runtime", _valid_result())
    ProviderRegistry.register("private_ai_runtime", lambda db: adapter)

    result = await ProviderRuntimeRouter(mock_db).route(_request())

    assert result.error_code == "provider_canary_control_path"
    assert adapter.calls == 0


@pytest.mark.asyncio
async def test_shadow_mode_calls_candidate_but_never_returns_customer_output(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "shadow")
    mock_db = _mock_db(
        {
            "primary_provider": "private_ai_runtime",
            "fallback_providers": [],
            "output_contract": "nexus_webchat_runtime_reply_v1",
            "timeout_ms": 3000,
            "kill_switch": False,
            "canary_percent": 100,
        }
    )
    adapter = DummyAdapter("private_ai_runtime", _valid_result())
    ProviderRegistry.register("private_ai_runtime", lambda db: adapter)

    result = await ProviderRuntimeRouter(mock_db).route(_request())

    assert not result.ok
    assert result.error_code == "provider_shadow_completed"
    assert result.structured_output is None
    assert adapter.calls == 1
    assert mock_db.audit_rows[-1]["operation"] == "shadow_generate"
    assert mock_db.audit_rows[-1]["status"] == "shadow_ok"
    summary = json.loads(mock_db.audit_rows[-1]["safe_summary"])
    assert summary["traffic_selection"]["authoritative"] is False


@pytest.mark.asyncio
async def test_kill_switch_overrides_full_canary_and_shadow(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "shadow")
    mock_db = _mock_db(
        {
            "primary_provider": "private_ai_runtime",
            "fallback_providers": [],
            "output_contract": "nexus_webchat_runtime_reply_v1",
            "timeout_ms": 3000,
            "kill_switch": True,
            "canary_percent": 100,
        }
    )
    adapter = DummyAdapter("private_ai_runtime", _valid_result())
    ProviderRegistry.register("private_ai_runtime", lambda db: adapter)

    result = await ProviderRuntimeRouter(mock_db).route(_request())

    assert result.error_code == "kill_switch_active"
    assert adapter.calls == 0
    summary = json.loads(mock_db.audit_rows[-1]["safe_summary"])
    assert summary["traffic_selection"]["path"] == "kill_switch"


@pytest.mark.asyncio
async def test_invalid_traffic_mode_fails_closed_without_provider_call(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "invalid")
    mock_db = _mock_db(
        {
            "primary_provider": "private_ai_runtime",
            "fallback_providers": [],
            "output_contract": "nexus_webchat_runtime_reply_v1",
            "timeout_ms": 3000,
            "kill_switch": False,
            "canary_percent": 100,
        }
    )
    adapter = DummyAdapter("private_ai_runtime", _valid_result())
    ProviderRegistry.register("private_ai_runtime", lambda db: adapter)

    result = await ProviderRuntimeRouter(mock_db).route(_request())

    assert result.error_code == "provider_runtime_traffic_mode_invalid"
    assert adapter.calls == 0


@pytest.mark.asyncio
async def test_provider_runtime_router_parse_reject_returns_no_customer_reply():
    mock_db = _mock_db(
        {
            "primary_provider": "private_ai_runtime",
            "fallback_providers": [],
            "output_contract": "nexus_webchat_runtime_reply_v1",
            "timeout_ms": 3000,
            "kill_switch": False,
            "canary_percent": 100,
        }
    )
    adapter = DummyAdapter(
        "private_ai_runtime",
        ProviderResult(
            ok=True,
            provider="private_ai_runtime",
            elapsed_ms=100,
            structured_output={"customer_reply": "hi"},
        ),
    )
    ProviderRegistry.register("private_ai_runtime", lambda db: adapter)

    result = await ProviderRuntimeRouter(mock_db).route(_request())

    assert not result.ok
    assert result.error_code == "all_providers_failed"
    assert mock_db.execute.call_count == 3


@pytest.mark.asyncio
async def test_provider_runtime_router_accepts_trusted_tracking_followup_with_unrelated_locked_fact():
    mock_db = _mock_db(
        {
            "primary_provider": "private_ai_runtime",
            "fallback_providers": [],
            "output_contract": "nexus_webchat_runtime_reply_v1",
            "timeout_ms": 3000,
            "kill_switch": False,
            "canary_percent": 100,
        }
    )
    adapter = DummyAdapter(
        "private_ai_runtime",
        ProviderResult(
            ok=True,
            provider="private_ai_runtime",
            elapsed_ms=100,
            structured_output={
                "customer_reply": "Your parcel ending 007813 has been delivered. If the recipient cannot find it, please check with reception or the delivery contact point, then ask us for human review.",
                "language": "en",
                "intent": "tracking",
                "tracking_number": "CH020000007813",
                "handoff_required": False,
                "ticket_should_create": False,
            },
        ),
    )
    ProviderRegistry.register("private_ai_runtime", lambda db: adapter)

    result = await ProviderRuntimeRouter(mock_db).route(_trusted_tracking_followup_request())

    assert result.ok
    assert "007813" in result.structured_output["customer_reply"]


def test_provider_runtime_routes_remain_mounted_and_retired_routes_absent():
    from app.main import app

    routes = sorted(getattr(route, "path", "") for route in app.routes)
    assert "/api/admin/provider-runtime/status" in routes, routes
    assert "/api/webchat/init" in routes, routes
    assert not any("provider-credentials/codex" in path for path in routes), routes
    assert not any("webchat-fast" in path or "fast-reply" in path for path in routes), routes
