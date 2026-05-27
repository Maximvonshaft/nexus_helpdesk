from __future__ import annotations

from unittest.mock import Mock

import pytest

from app.services.provider_runtime.registry import ProviderAdapter, ProviderRegistry
from app.services.provider_runtime.router import ProviderRuntimeRouter
from app.services.provider_runtime.schemas import ProviderRequest, ProviderResult


class E2EAdapter(ProviderAdapter):
    def __init__(self, name: str, result: ProviderResult):
        self.name = name
        self.result = result
        self.calls = 0

    async def generate(self, db, request):
        self.calls += 1
        return self.result


def _success(provider: str, reply: str = "I can help with that.") -> ProviderResult:
    return ProviderResult(
        ok=True,
        provider=provider,
        elapsed_ms=11,
        structured_output={
            "reply": reply,
            "intent": "other",
            "tracking_number": None,
            "handoff_required": False,
            "handoff_reason": None,
            "recommended_agent_action": None,
        },
        raw_payload_safe_summary={"status": "ok"},
    )


def _approved_shipping_sla_context(answer: str) -> dict:
    return {
        "knowledge_context": {
            "locked_facts": [
                {
                    "item_key": "fact.ch.shipping-sla",
                    "title": "瑞士海运时效",
                    "question": "瑞士海运时效是多少？",
                    "answer": answer,
                    "answer_mode": "direct_answer",
                    "source": {"item_key": "fact.ch.shipping-sla", "title": "瑞士海运时效"},
                }
            ],
            "hits": [
                {
                    "item_key": "fact.ch.shipping-sla",
                    "title": "瑞士海运时效",
                    "score": 42.0,
                    "chunk_index": 0,
                    "retrieval_method": "structured_fact_recall+direct_answer_fact",
                    "direct_answer": answer,
                    "answer_mode": "direct_answer",
                    "metadata": {
                        "knowledge_kind": "business_fact",
                        "fact_status": "approved",
                        "answer_mode": "direct_answer",
                    },
                    "source_metadata": {"item_key": "fact.ch.shipping-sla"},
                }
            ],
            "grounding_would_apply": True,
            "grounding_source": {"item_key": "fact.ch.shipping-sla"},
        }
    }


def _request() -> ProviderRequest:
    return ProviderRequest(
        request_id="req-e2e",
        tenant_id="default",
        tenant_key="default",
        channel_key="website",
        session_id="sess-e2e",
        scenario="webchat_fast_reply",
        body="hello",
        output_contract="speedaf_webchat_fast_reply_v1",
        timeout_ms=10000,
    )


def _shipping_sla_request(answer: str) -> ProviderRequest:
    req = _request()
    req.body = "瑞士海运时效是多少？"
    req.metadata = _approved_shipping_sla_context(answer)
    return req


def _db(rule: dict):
    db = Mock()
    select_result = Mock()
    select_result.mappings.return_value.first.return_value = rule
    audit_rows: list[dict] = []

    def execute(stmt, params=None, *args, **kwargs):
        if "INSERT INTO provider_runtime_audit_logs" in str(stmt):
            audit_rows.append(params or {})
            return Mock()
        return select_result

    db.execute.side_effect = execute
    db.audit_rows = audit_rows
    return db


def _rule(*, canary_percent: int = 100, kill_switch: bool = False) -> dict:
    return {
        "primary_provider": "codex_app_server",
        "fallback_providers": ["openclaw_responses", "rule_engine"],
        "output_contract": "speedaf_webchat_fast_reply_v1",
        "timeout_ms": 10000,
        "kill_switch": kill_switch,
        "canary_percent": canary_percent,
    }


@pytest.mark.asyncio
async def test_codex_success_path_e2e(monkeypatch):
    import app.services.provider_runtime as provider_runtime_module

    monkeypatch.setattr(provider_runtime_module, "bootstrap_provider_runtime", lambda: None)
    codex = E2EAdapter("codex_app_server", _success("codex_app_server"))
    openclaw = E2EAdapter("openclaw_responses", _success("openclaw_responses"))
    ProviderRegistry.register("codex_app_server", lambda db: codex)
    ProviderRegistry.register("openclaw_responses", lambda db: openclaw)

    result = await ProviderRuntimeRouter(_db(_rule(canary_percent=100))).route(_request())

    assert result.ok is True
    assert result.provider == "codex_app_server"
    assert codex.calls == 1
    assert openclaw.calls == 0


@pytest.mark.asyncio
async def test_codex_failure_falls_back_to_openclaw_e2e(monkeypatch):
    import app.services.provider_runtime as provider_runtime_module

    monkeypatch.setattr(provider_runtime_module, "bootstrap_provider_runtime", lambda: None)
    codex = E2EAdapter("codex_app_server", ProviderResult.unavailable("codex_app_server", "bridge_not_ready", 9))
    openclaw = E2EAdapter("openclaw_responses", _success("openclaw_responses"))
    ProviderRegistry.register("codex_app_server", lambda db: codex)
    ProviderRegistry.register("openclaw_responses", lambda db: openclaw)

    result = await ProviderRuntimeRouter(_db(_rule(canary_percent=100))).route(_request())

    assert result.ok is True
    assert result.provider == "openclaw_responses"
    assert codex.calls == 1
    assert openclaw.calls == 1


@pytest.mark.asyncio
async def test_codex_upstream_timeout_falls_back_to_openclaw_e2e(monkeypatch):
    import app.services.provider_runtime as provider_runtime_module

    monkeypatch.setattr(provider_runtime_module, "bootstrap_provider_runtime", lambda: None)
    codex = E2EAdapter("codex_app_server", ProviderResult.unavailable("codex_app_server", "bridge_timeout", 15000))
    openclaw = E2EAdapter("openclaw_responses", _success("openclaw_responses"))
    ProviderRegistry.register("codex_app_server", lambda db: codex)
    ProviderRegistry.register("openclaw_responses", lambda db: openclaw)

    result = await ProviderRuntimeRouter(_db(_rule(canary_percent=100))).route(_request())

    assert result.ok is True
    assert result.provider == "openclaw_responses"
    assert codex.calls == 1
    assert openclaw.calls == 1


@pytest.mark.asyncio
async def test_kill_switch_bypasses_codex_e2e(monkeypatch):
    import app.services.provider_runtime as provider_runtime_module

    monkeypatch.setattr(provider_runtime_module, "bootstrap_provider_runtime", lambda: None)
    codex = E2EAdapter("codex_app_server", _success("codex_app_server"))
    openclaw = E2EAdapter("openclaw_responses", _success("openclaw_responses"))
    ProviderRegistry.register("codex_app_server", lambda db: codex)
    ProviderRegistry.register("openclaw_responses", lambda db: openclaw)

    result = await ProviderRuntimeRouter(_db(_rule(canary_percent=100, kill_switch=True))).route(_request())

    assert result.ok is True
    assert result.provider == "openclaw_responses"
    assert codex.calls == 0
    assert openclaw.calls == 1


@pytest.mark.asyncio
async def test_canary_zero_bypasses_codex_e2e(monkeypatch):
    import app.services.provider_runtime as provider_runtime_module

    monkeypatch.setattr(provider_runtime_module, "bootstrap_provider_runtime", lambda: None)
    codex = E2EAdapter("codex_app_server", _success("codex_app_server"))
    openclaw = E2EAdapter("openclaw_responses", _success("openclaw_responses"))
    ProviderRegistry.register("codex_app_server", lambda db: codex)
    ProviderRegistry.register("openclaw_responses", lambda db: openclaw)

    result = await ProviderRuntimeRouter(_db(_rule(canary_percent=0))).route(_request())

    assert result.ok is True
    assert result.provider == "openclaw_responses"
    assert codex.calls == 0
    assert openclaw.calls == 1


@pytest.mark.asyncio
async def test_audit_and_result_do_not_expose_raw_oauth_tokens_e2e(monkeypatch):
    import app.services.provider_runtime as provider_runtime_module

    monkeypatch.setattr(provider_runtime_module, "bootstrap_provider_runtime", lambda: None)
    codex = E2EAdapter(
        "codex_app_server",
        ProviderResult.unavailable(
            "codex_app_server",
            "bridge_not_ready",
            9,
        ),
    )
    openclaw = E2EAdapter("openclaw_responses", _success("openclaw_responses"))
    ProviderRegistry.register("codex_app_server", lambda db: codex)
    ProviderRegistry.register("openclaw_responses", lambda db: openclaw)
    db = _db(_rule(canary_percent=100))

    result = await ProviderRuntimeRouter(db).route(_request())
    rendered = f"{result.model_dump()} {db.audit_rows}"

    assert result.ok is True
    assert "raw-access-token" not in rendered
    assert "raw-refresh-token" not in rendered
    assert "access_token" not in rendered
    assert "refresh_token" not in rendered


@pytest.mark.asyncio
async def test_router_accepts_grounded_business_sla_status_language(monkeypatch):
    import app.services.provider_runtime as provider_runtime_module

    monkeypatch.setattr(provider_runtime_module, "bootstrap_provider_runtime", lambda: None)
    answer = "瑞士海运清关时效为 15 天。"
    codex = E2EAdapter("codex_app_server", _success("codex_app_server", reply=answer))
    ProviderRegistry.register("codex_app_server", lambda db: codex)
    db = _db(_rule(canary_percent=100))

    result = await ProviderRuntimeRouter(db).route(_shipping_sla_request(answer))

    assert result.ok is True
    assert result.structured_output["customer_reply"] == answer
    assert codex.calls == 1
    assert not any(row.get("error_code") == "parse_reject" for row in db.audit_rows)
    assert not any(row.get("error_code") == "all_providers_failed" for row in db.audit_rows)


@pytest.mark.asyncio
async def test_router_rejects_live_tracking_status_without_evidence_even_with_kb(monkeypatch):
    import app.services.provider_runtime as provider_runtime_module

    monkeypatch.setattr(provider_runtime_module, "bootstrap_provider_runtime", lambda: None)
    answer = "瑞士海运清关时效为 15 天。"
    codex = E2EAdapter("codex_app_server", _success("codex_app_server", reply="你的包裹正在运输中。"))
    ProviderRegistry.register("codex_app_server", lambda db: codex)
    db = _db({**_rule(canary_percent=100), "fallback_providers": []})

    result = await ProviderRuntimeRouter(db).route(_shipping_sla_request(answer))

    assert result.ok is False
    assert result.error_code == "all_providers_failed"
    parse_rejects = [row for row in db.audit_rows if row.get("error_code") == "parse_reject"]
    assert parse_rejects
    assert "trusted tracking evidence" in parse_rejects[0]["safe_summary"]
