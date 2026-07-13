from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.services import provider_runtime as provider_runtime_module
from app.services.provider_runtime.registry import ProviderAdapter, ProviderRegistry
from app.services.provider_runtime.router import ProviderRuntimeRouter
from app.services.provider_runtime.schemas import ProviderRequest, ProviderResult
from app.services.provider_runtime.traffic_selection import (
    effective_canary_percent,
    safe_traffic_configuration,
    select_provider_traffic,
)


class _FailingAdapter(ProviderAdapter):
    name = "private_ai_runtime"

    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, db, request):
        self.calls += 1
        return ProviderResult.unavailable(
            self.name,
            "synthetic_candidate_failure",
            1,
            fallback_allowed=False,
        )


class _InvalidOutputAdapter(ProviderAdapter):
    name = "private_ai_runtime"

    def __init__(self, marker: str) -> None:
        self.marker = marker
        self.calls = 0

    async def generate(self, db, request):
        self.calls += 1
        return ProviderResult(
            ok=True,
            provider=self.name,
            elapsed_ms=1,
            structured_output={
                "customer_reply": "safe public reply",
                "language": "en",
                "intent": self.marker,
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


def _request(
    *,
    scenario: str = "review_traffic_test",
    output_contract: str = "synthetic_contract",
) -> ProviderRequest:
    return ProviderRequest(
        request_id="review-regression-request",
        tenant_id="tenant-1",
        tenant_key="tenant-key-1",
        channel_key="website",
        session_id="session-1",
        scenario=scenario,
        body="hello",
        output_contract=output_contract,
        timeout_ms=1000,
    )


def _sqlite_session(
    *,
    kill_switch: int,
    scenario: str = "review_traffic_test",
    output_contract: str = "synthetic_contract",
) -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE provider_routing_rules (
                    primary_provider TEXT NOT NULL,
                    fallback_providers TEXT NOT NULL,
                    output_contract TEXT NOT NULL,
                    timeout_ms INTEGER NOT NULL,
                    kill_switch BOOLEAN NOT NULL,
                    canary_percent INTEGER NOT NULL,
                    tenant_id TEXT NOT NULL,
                    channel_key TEXT NOT NULL,
                    scenario TEXT NOT NULL,
                    enabled BOOLEAN NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE provider_runtime_audit_logs (
                    id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    channel_key TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    status TEXT NOT NULL,
                    safe_summary TEXT NOT NULL,
                    error_code TEXT,
                    elapsed_ms INTEGER NOT NULL,
                    created_at TIMESTAMP NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO provider_routing_rules (
                    primary_provider, fallback_providers, output_contract, timeout_ms,
                    kill_switch, canary_percent, tenant_id, channel_key, scenario, enabled
                ) VALUES (
                    'private_ai_runtime', '[]', :output_contract, 3000,
                    :kill_switch, 100, 'tenant-1', 'website', :scenario, 1
                )
                """
            ),
            {
                "kill_switch": kill_switch,
                "scenario": scenario,
                "output_contract": output_contract,
            },
        )
    return Session(engine)


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
async def test_sqlite_false_boolean_is_typed_before_strict_validation():
    db = _sqlite_session(kill_switch=0)
    adapter = _FailingAdapter()
    ProviderRegistry.register(adapter.name, lambda _db: adapter)

    try:
        result = await ProviderRuntimeRouter(db).route(_request())
        audit_summary = json.loads(
            db.execute(
                text("SELECT safe_summary FROM provider_runtime_audit_logs ORDER BY created_at DESC LIMIT 1")
            ).scalar_one()
        )
    finally:
        db.close()

    assert result.error_code == "synthetic_candidate_failure"
    assert adapter.calls == 1
    assert audit_summary["traffic_selection"]["path"] == "canary_authoritative"


@pytest.mark.asyncio
async def test_sqlite_true_boolean_keeps_kill_switch_precedence():
    db = _sqlite_session(kill_switch=1)
    adapter = _FailingAdapter()
    ProviderRegistry.register(adapter.name, lambda _db: adapter)

    try:
        result = await ProviderRuntimeRouter(db).route(_request())
        audit_summary = json.loads(
            db.execute(
                text("SELECT safe_summary FROM provider_runtime_audit_logs ORDER BY created_at DESC LIMIT 1")
            ).scalar_one()
        )
    finally:
        db.close()

    assert result.error_code == "kill_switch_active"
    assert adapter.calls == 0
    assert audit_summary["traffic_selection"]["path"] == "kill_switch"


@pytest.mark.asyncio
async def test_parse_reject_audit_never_persists_exception_or_output_text():
    marker = "customer-secret-marker-582"
    scenario = "webchat_runtime_reply"
    output_contract = "nexus_webchat_runtime_reply_v1"
    db = _sqlite_session(
        kill_switch=0,
        scenario=scenario,
        output_contract=output_contract,
    )
    adapter = _InvalidOutputAdapter(marker)
    ProviderRegistry.register(adapter.name, lambda _db: adapter)

    try:
        result = await ProviderRuntimeRouter(db).route(
            _request(scenario=scenario, output_contract=output_contract)
        )
        raw_summaries = db.execute(
            text("SELECT safe_summary FROM provider_runtime_audit_logs ORDER BY created_at ASC")
        ).scalars().all()
    finally:
        db.close()

    assert result.error_code == "all_providers_failed"
    assert adapter.calls == 1
    serialized = "\n".join(str(item) for item in raw_summaries)
    assert marker not in serialized
    summaries = [json.loads(item) for item in raw_summaries]
    parse_reject = next(item for item in summaries if item.get("parse_error_code"))
    assert parse_reject["parse_error_code"] == "output_contract_rejected"
    assert "parse_error" not in parse_reject
