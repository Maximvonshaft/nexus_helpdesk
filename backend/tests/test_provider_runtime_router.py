from __future__ import annotations

from unittest.mock import Mock

import pytest

from app.services import provider_runtime as provider_runtime_module
from app.services.provider_runtime.registry import ProviderAdapter, ProviderRegistry
from app.services.provider_runtime.router import ProviderRuntimeRouter
from app.services.provider_runtime.schemas import ProviderRequest, ProviderResult


class DummyAdapter(ProviderAdapter):
    def __init__(self, name: str, result: ProviderResult):
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
        "PROVIDER_RUNTIME_PRIMARY_PROVIDER",
        "PROVIDER_RUNTIME_FALLBACK_PROVIDERS",
        "PROVIDER_RUNTIME_OUTPUT_CONTRACT",
        "PROVIDER_RUNTIME_TIMEOUT_MS",
    ):
        monkeypatch.delenv(name, raising=False)
    yield


def _mock_db(rule: dict | None):
    mock_db = Mock()
    mock_rule = Mock()
    mock_rule.mappings.return_value.first.return_value = rule

    def mock_db_execute(stmt, params=None, *args, **kwargs):
        query = str(stmt).lower()
        if "insert into provider_runtime_audit_logs" in query:
            return Mock()
        return mock_rule

    mock_db.execute.side_effect = mock_db_execute
    return mock_db


def _request(*, request_id: str = "req1", session_id: str = "s1") -> ProviderRequest:
    return ProviderRequest(
        request_id=request_id,
        tenant_id="t1",
        tenant_key="tk1",
        channel_key="c1",
        session_id=session_id,
        scenario="webchat_runtime_reply",
        body="hello",
        output_contract="nexus.webchat_runtime_reply",
        timeout_ms=1000,
    )


def _rule(*, canary_percent: int = 100, kill_switch: bool = False) -> dict:
    return {
        "primary_provider": "private_ai_runtime",
        "fallback_providers": [],
        "output_contract": "nexus.webchat_runtime_reply",
        "timeout_ms": 3000,
        "kill_switch": kill_switch,
        "canary_percent": canary_percent,
    }


def _success_result() -> ProviderResult:
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


def _register_adapter(result: ProviderResult | None = None) -> DummyAdapter:
    adapter = DummyAdapter("private_ai_runtime", result or _success_result())
    ProviderRegistry.register("private_ai_runtime", lambda db: adapter)
    return adapter


@pytest.mark.asyncio
async def test_no_rule_is_control_path_and_never_calls_provider():
    db = _mock_db(None)
    adapter = _register_adapter()

    result = await ProviderRuntimeRouter(db).route(_request())

    assert result.ok is False
    assert result.error_code == "provider_canary_control_path"
    assert result.raw_payload_safe_summary["traffic"]["configured_mode"] == "control"
    assert result.raw_payload_safe_summary["traffic"]["canary_percent"] == 0
    assert adapter.calls == 0


@pytest.mark.asyncio
async def test_full_canary_executes_once_and_returns_authoritative_result(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "canary")
    db = _mock_db(_rule(canary_percent=100))
    adapter = _register_adapter()

    result = await ProviderRuntimeRouter(db).route(_request())

    assert result.ok is True
    assert result.provider == "private_ai_runtime"
    assert result.structured_output["customer_reply"] == "hi"
    traffic = result.raw_payload_safe_summary["traffic"]
    assert traffic["path"] == "canary_authoritative"
    assert traffic["authoritative"] is True
    assert adapter.calls == 1


@pytest.mark.asyncio
async def test_zero_percent_canary_never_calls_provider(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "canary")
    db = _mock_db(_rule(canary_percent=0))
    adapter = _register_adapter()

    result = await ProviderRuntimeRouter(db).route(_request())

    assert result.ok is False
    assert result.error_code == "provider_canary_control_path"
    assert adapter.calls == 0


@pytest.mark.asyncio
async def test_zero_percent_shadow_never_calls_provider(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "shadow")
    db = _mock_db(_rule(canary_percent=0))
    adapter = _register_adapter()

    result = await ProviderRuntimeRouter(db).route(_request())

    assert result.ok is False
    assert result.error_code == "provider_canary_control_path"
    assert result.raw_payload_safe_summary["traffic"]["configured_mode"] == "shadow"
    assert adapter.calls == 0


@pytest.mark.asyncio
async def test_kill_switch_precedes_full_canary(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "canary")
    db = _mock_db(_rule(canary_percent=100, kill_switch=True))
    adapter = _register_adapter()

    result = await ProviderRuntimeRouter(db).route(_request())

    assert result.ok is False
    assert result.error_code == "kill_switch_active"
    assert result.raw_payload_safe_summary["traffic"]["path"] == "kill_switch"
    assert adapter.calls == 0


@pytest.mark.asyncio
async def test_environment_false_cannot_clear_persisted_kill_switch(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "canary")
    monkeypatch.setenv("PROVIDER_RUNTIME_KILL_SWITCH", "false")
    db = _mock_db(_rule(canary_percent=100, kill_switch=True))
    adapter = _register_adapter()

    result = await ProviderRuntimeRouter(db).route(_request())

    assert result.error_code == "kill_switch_active"
    assert adapter.calls == 0


@pytest.mark.asyncio
async def test_shadow_executes_but_never_returns_candidate_authority(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "shadow")
    db = _mock_db(_rule(canary_percent=100))
    adapter = _register_adapter()

    result = await ProviderRuntimeRouter(db).route(_request())

    assert result.ok is False
    assert result.error_code == "provider_shadow_only"
    assert result.structured_output is None
    assert result.raw_payload_safe_summary["traffic"]["path"] == "shadow_only"
    assert result.raw_payload_safe_summary["traffic"]["authoritative"] is False
    assert adapter.calls == 1


@pytest.mark.asyncio
async def test_invalid_canary_configuration_fails_closed(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "canary")
    db = _mock_db(_rule(canary_percent=10))
    adapter = _register_adapter()

    result = await ProviderRuntimeRouter(db).route(_request())

    assert result.ok is False
    assert result.error_code == "provider_runtime_configuration_invalid"
    assert adapter.calls == 0


@pytest.mark.asyncio
async def test_malformed_fallback_configuration_fails_closed():
    rule = _rule(canary_percent=100)
    rule["fallback_providers"] = {"unexpected": "mapping"}
    db = _mock_db(rule)
    adapter = _register_adapter()

    result = await ProviderRuntimeRouter(db).route(_request())

    assert result.ok is False
    assert result.error_code == "provider_runtime_configuration_invalid"
    assert adapter.calls == 0
    assert (
        result.raw_payload_safe_summary["traffic"]["reason"]
        == "provider_runtime_fallback_provider_invalid"
    )


@pytest.mark.asyncio
async def test_unknown_output_contract_never_reaches_registered_adapter(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "canary")
    rule = _rule(canary_percent=100)
    rule["output_contract"] = "nexus.unknown.contract"
    db = _mock_db(rule)
    adapter = _register_adapter()

    result = await ProviderRuntimeRouter(db).route(_request())

    assert result.ok is False
    assert result.error_code == "provider_runtime_output_contract_invalid"
    assert result.fallback_allowed is False
    assert result.raw_payload_safe_summary["traffic"]["path"] == "canary_authoritative"
    assert adapter.calls == 0


@pytest.mark.asyncio
async def test_parse_reject_returns_no_customer_reply(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "canary")
    db = _mock_db(_rule(canary_percent=100))
    adapter = _register_adapter(
        ProviderResult(
            ok=True,
            provider="private_ai_runtime",
            elapsed_ms=100,
            structured_output={"customer_reply": "hi"},
        )
    )

    result = await ProviderRuntimeRouter(db).route(_request())

    assert result.ok is False
    assert result.error_code == "all_providers_failed"
    assert adapter.calls == 1


@pytest.mark.asyncio
async def test_canary_bucket_is_stable_when_request_id_changes(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "canary")
    db_a = _mock_db(_rule(canary_percent=25))
    db_b = _mock_db(_rule(canary_percent=25))
    adapter = _register_adapter()

    first = await ProviderRuntimeRouter(db_a).route(
        _request(request_id="request-a", session_id="stable-session")
    )
    second = await ProviderRuntimeRouter(db_b).route(
        _request(request_id="request-b", session_id="stable-session")
    )

    assert first.error_code == second.error_code
    assert (
        first.raw_payload_safe_summary["traffic"]["bucket"]
        == second.raw_payload_safe_summary["traffic"]["bucket"]
    )
    assert adapter.calls in {0, 2}
