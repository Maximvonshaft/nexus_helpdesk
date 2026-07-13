from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from app.services import provider_runtime as provider_runtime_module
from app.services.provider_runtime.registry import ProviderAdapter, ProviderRegistry
from app.services.provider_runtime.router import ProviderRuntimeRouter
from app.services.provider_runtime.schemas import ProviderRequest, ProviderResult
from app.services.provider_runtime.traffic_selection import (
    effective_canary_percent,
    safe_traffic_configuration,
    select_provider_traffic,
)


class _Adapter(ProviderAdapter):
    name = "private_ai_runtime"

    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, db, request):
        self.calls += 1
        return ProviderResult(
            ok=True,
            provider=self.name,
            elapsed_ms=1,
            structured_output={
                "customer_reply": "safe response",
                "language": "en",
                "intent": "greeting",
                "handoff_required": False,
                "ticket_should_create": False,
            },
        )


@pytest.fixture(autouse=True)
def _isolate_runtime(monkeypatch):
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
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "canary")


def _request() -> ProviderRequest:
    return ProviderRequest(
        request_id="review-regression-request",
        tenant_id="tenant-1",
        tenant_key="tenant-key-1",
        channel_key="website",
        session_id="session-1",
        scenario="webchat_runtime_reply",
        body="hello",
        output_contract="nexus_webchat_runtime_reply_v1",
        timeout_ms=1000,
    )


def _db_with_rule(*, kill_switch):
    rule = {
        "primary_provider": "private_ai_runtime",
        "fallback_providers": [],
        "output_contract": "nexus_webchat_runtime_reply_v1",
        "timeout_ms": 3000,
        "kill_switch": kill_switch,
        "canary_percent": 100,
    }
    db = Mock()
    rule_result = Mock()
    rule_result.mappings.return_value.first.return_value = rule
    audit_rows: list[dict] = []

    def execute(statement, params, *args, **kwargs):
        if "insert into provider_runtime_audit_logs" in str(statement).lower():
            audit_rows.append(dict(params))
            return Mock()
        return rule_result

    db.execute.side_effect = execute
    db.audit_rows = audit_rows
    return db


@pytest.mark.parametrize("value", [2, 50, 99, "2"])
def test_only_governed_canary_rollout_percentages_are_accepted(monkeypatch, value):
    monkeypatch.delenv("PROVIDER_RUNTIME_CANARY_PERCENT", raising=False)

    with pytest.raises(ValueError, match="provider_runtime_canary_percent_invalid"):
        effective_canary_percent(value)
    with pytest.raises(ValueError, match="provider_runtime_canary_percent_invalid"):
        select_provider_traffic(
            _request(),
            canary_percent=value,
            kill_switch=False,
            configured_mode_value="canary",
        )


@pytest.mark.parametrize("value", ["2", "50", "99"])
def test_unsupported_canary_environment_override_fails_closed(monkeypatch, value):
    monkeypatch.setenv("PROVIDER_RUNTIME_CANARY_PERCENT", value)

    with pytest.raises(ValueError, match="provider_runtime_canary_percent_invalid"):
        effective_canary_percent(0)

    summary = safe_traffic_configuration(default_canary_percent=0, default_kill_switch=False)
    assert summary["canary_percent"] is None
    assert summary["configuration_errors"] == ["provider_runtime_canary_percent_invalid"]


@pytest.mark.asyncio
async def test_sqlite_false_boolean_is_normalized_before_strict_validation():
    db = _db_with_rule(kill_switch=0)
    adapter = _Adapter()
    ProviderRegistry.register(adapter.name, lambda _db: adapter)

    result = await ProviderRuntimeRouter(db).route(_request())

    assert result.ok is True
    assert adapter.calls == 1
    assert json.loads(db.audit_rows[-1]["safe_summary"])["traffic_selection"]["path"] == "canary_authoritative"


@pytest.mark.asyncio
async def test_sqlite_true_boolean_keeps_kill_switch_precedence():
    db = _db_with_rule(kill_switch=1)
    adapter = _Adapter()
    ProviderRegistry.register(adapter.name, lambda _db: adapter)

    result = await ProviderRuntimeRouter(db).route(_request())

    assert result.ok is False
    assert result.error_code == "kill_switch_active"
    assert adapter.calls == 0
    traffic = json.loads(db.audit_rows[-1]["safe_summary"])["traffic_selection"]
    assert traffic["path"] == "kill_switch"
