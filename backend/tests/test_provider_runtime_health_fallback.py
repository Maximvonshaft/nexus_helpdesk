from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from app.services.provider_runtime.health import ProviderRuntimeHealth
from app.services.provider_runtime.registry import ProviderAdapter, ProviderRegistry
from app.services.provider_runtime.router import ProviderRuntimeRouter
from app.services.provider_runtime.schemas import ProviderRequest, ProviderResult


def _request(**overrides) -> ProviderRequest:
    data = {
        "request_id": "req-health-1",
        "tenant_id": "default",
        "tenant_key": "default",
        "channel_key": "website",
        "session_id": "sess-health-1",
        "scenario": "webchat_fast_reply",
        "body": "Where is my parcel?",
        "recent_context": [],
        "tracking_fact_summary": "Trusted tracking fact: in transit.",
        "tracking_fact_evidence_present": True,
        "output_contract": "speedaf_webchat_fast_reply_v1",
        "timeout_ms": 26000,
        "metadata": {"knowledge_context": {"hits": [], "locked_facts": []}},
    }
    data.update(overrides)
    return ProviderRequest(**data)


class CapturingDb:
    def __init__(self):
        self.audit_rows: list[dict] = []
        self.rule = Mock()
        self.rule.mappings.return_value.first.return_value = None

    def execute(self, stmt, params=None, *args, **kwargs):
        query = str(stmt).lower()
        if "insert into provider_runtime_audit_logs" in query:
            self.audit_rows.append(dict(params or {}))
            return Mock()
        return self.rule

    def commit(self):
        return None

    def rollback(self):
        return None


class CountingFailureAdapter(ProviderAdapter):
    def __init__(self, name: str, error_code: str):
        self.name = name
        self.error_code = error_code
        self.calls = 0

    async def generate(self, db, req):
        self.calls += 1
        return ProviderResult.unavailable(self.name, self.error_code, 10, fallback_allowed=True)


class CountingSuccessAdapter(ProviderAdapter):
    def __init__(self, name: str):
        self.name = name
        self.calls = 0

    async def generate(self, db, req):
        self.calls += 1
        return ProviderResult(
            ok=True,
            provider=self.name,
            raw_provider=self.name,
            reply_source=self.name,
            model="unit-test-model",
            elapsed_ms=5,
            structured_output={
                "customer_reply": "Your parcel is currently in transit.",
                "reply": "Your parcel is currently in transit.",
                "language": "en",
                "intent": "tracking",
                "tracking_number": None,
                "handoff_required": False,
                "handoff_reason": None,
                "recommended_agent_action": None,
                "ticket_should_create": False,
                "tool_calls": [],
                "evidence_used": [],
                "confidence": 0.9,
                "reason": "trusted tracking fact present",
                "risk_level": "low",
                "next_action": "reply",
                "safety_notes": [],
            },
            raw_payload_safe_summary={"unit": True},
        )


@pytest.fixture(autouse=True)
def reset_health(monkeypatch):
    ProviderRuntimeHealth.reset_for_tests()
    monkeypatch.delenv("PROVIDER_RUNTIME_HEALTH_FALLBACK_ENABLED", raising=False)
    monkeypatch.delenv("PROVIDER_RUNTIME_HEALTH_FAILURE_THRESHOLD", raising=False)
    monkeypatch.delenv("PROVIDER_RUNTIME_HEALTH_FAILURE_WINDOW_SECONDS", raising=False)
    monkeypatch.delenv("PROVIDER_RUNTIME_HEALTH_COOLDOWN_SECONDS", raising=False)
    monkeypatch.delenv("PROVIDER_RUNTIME_PRIMARY_PROVIDER", raising=False)
    monkeypatch.delenv("PROVIDER_RUNTIME_FALLBACK_PROVIDERS", raising=False)
    monkeypatch.delenv("WEBCHAT_FAST_AI_FALLBACK_PROVIDER", raising=False)
    monkeypatch.setenv("APP_ENV", "test")
    yield
    ProviderRuntimeHealth.reset_for_tests()


@pytest.mark.asyncio
async def test_codex_direct_defaults_to_openai_responses_before_rule_engine(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_PRIMARY_PROVIDER", "codex_direct")
    monkeypatch.setenv("PROVIDER_RUNTIME_HEALTH_FAILURE_THRESHOLD", "5")

    import app.services.provider_runtime as provider_runtime_module

    monkeypatch.setattr(provider_runtime_module, "bootstrap_provider_runtime", lambda: None)
    codex = CountingFailureAdapter("codex_direct", "codex_direct_timeout")
    openai = CountingSuccessAdapter("openai_responses")
    rule = CountingSuccessAdapter("rule_engine")
    ProviderRegistry.register("codex_direct", lambda db: codex)
    ProviderRegistry.register("openai_responses", lambda db: openai)
    ProviderRegistry.register("rule_engine", lambda db: rule)

    result = await ProviderRuntimeRouter(CapturingDb()).route(_request())

    assert result.ok is True
    assert result.provider == "openai_responses"
    assert codex.calls == 1
    assert openai.calls == 1
    assert rule.calls == 0


@pytest.mark.asyncio
async def test_codex_direct_timeout_sets_cooldown_and_skips_primary(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_PRIMARY_PROVIDER", "codex_direct")
    monkeypatch.setenv("PROVIDER_RUNTIME_HEALTH_FAILURE_THRESHOLD", "2")
    monkeypatch.setenv("PROVIDER_RUNTIME_HEALTH_COOLDOWN_SECONDS", "300")

    import app.services.provider_runtime as provider_runtime_module

    monkeypatch.setattr(provider_runtime_module, "bootstrap_provider_runtime", lambda: None)
    codex = CountingFailureAdapter("codex_direct", "codex_direct_timeout")
    openai = CountingSuccessAdapter("openai_responses")
    ProviderRegistry.register("codex_direct", lambda db: codex)
    ProviderRegistry.register("openai_responses", lambda db: openai)
    ProviderRegistry.register("rule_engine", lambda db: CountingFailureAdapter("rule_engine", "rule_engine_skeleton_unavailable"))

    db = CapturingDb()
    first = await ProviderRuntimeRouter(db).route(_request(request_id="req-1"))
    second = await ProviderRuntimeRouter(db).route(_request(request_id="req-2"))
    third = await ProviderRuntimeRouter(db).route(_request(request_id="req-3"))

    assert first.ok is True
    assert second.ok is True
    assert third.ok is True
    assert codex.calls == 2
    assert openai.calls == 3

    skipped = [row for row in db.audit_rows if row.get("operation") == "generate" and row.get("status") == "skipped" and row.get("provider") == "codex_direct"]
    assert skipped
    assert skipped[-1]["error_code"] == "provider_in_cooldown"
    safe_summary = json.loads(skipped[-1]["safe_summary"])
    assert safe_summary["provider_health"]["health_skip"] is True


@pytest.mark.asyncio
async def test_health_fallback_can_be_disabled(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_PRIMARY_PROVIDER", "codex_direct")
    monkeypatch.setenv("PROVIDER_RUNTIME_HEALTH_FALLBACK_ENABLED", "false")
    monkeypatch.setenv("PROVIDER_RUNTIME_HEALTH_FAILURE_THRESHOLD", "1")

    import app.services.provider_runtime as provider_runtime_module

    monkeypatch.setattr(provider_runtime_module, "bootstrap_provider_runtime", lambda: None)
    codex = CountingFailureAdapter("codex_direct", "codex_direct_timeout")
    openai = CountingSuccessAdapter("openai_responses")
    ProviderRegistry.register("codex_direct", lambda db: codex)
    ProviderRegistry.register("openai_responses", lambda db: openai)
    ProviderRegistry.register("rule_engine", lambda db: CountingFailureAdapter("rule_engine", "rule_engine_skeleton_unavailable"))

    await ProviderRuntimeRouter(CapturingDb()).route(_request(request_id="req-1"))
    await ProviderRuntimeRouter(CapturingDb()).route(_request(request_id="req-2"))

    assert codex.calls == 2
    assert openai.calls == 2
