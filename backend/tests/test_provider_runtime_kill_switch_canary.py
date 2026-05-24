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


def _request(*, session_id: str = "sess1", request_id: str = "req1", channel_key: str = "website") -> ProviderRequest:
    return ProviderRequest(
        request_id=request_id,
        tenant_id="default",
        tenant_key="default",
        channel_key=channel_key,
        session_id=session_id,
        scenario="webchat_fast_reply",
        body="hello",
        output_contract="speedaf_webchat_fast_reply_v1",
        timeout_ms=10000,
    )


def _rule(*, canary_percent: int, kill_switch: bool = False):
    return {
        "primary_provider": "codex_app_server",
        "fallback_providers": ["openclaw_responses", "rule_engine"],
        "output_contract": "speedaf_webchat_fast_reply_v1",
        "timeout_ms": 10000,
        "kill_switch": kill_switch,
        "canary_percent": canary_percent,
    }


def _session_for_bucket(expected_bucket: int) -> str:
    for idx in range(20000):
        session_id = f"codex-canary-session-{idx}"
        bucket = ProviderRuntimeRouter._stable_percent_bucket("default", "website", session_id)
        if bucket == expected_bucket:
            return session_id
    raise AssertionError(f"no deterministic session found for bucket {expected_bucket}")


def _session_above_bucket(min_bucket: int) -> str:
    for idx in range(20000):
        session_id = f"codex-canary-skip-session-{idx}"
        bucket = ProviderRuntimeRouter._stable_percent_bucket("default", "website", session_id)
        if bucket >= min_bucket:
            return session_id
    raise AssertionError(f"no deterministic session found for bucket >= {min_bucket}")


@pytest.mark.asyncio
async def test_canary_zero_routes_to_openclaw_without_codex(monkeypatch):
    import app.services.provider_runtime as provider_runtime_module

    monkeypatch.setattr(provider_runtime_module, "bootstrap_provider_runtime", lambda: None)
    ProviderRegistry.register("codex_app_server", lambda db: SuccessAdapter("codex_app_server"))
    ProviderRegistry.register("openclaw_responses", lambda db: SuccessAdapter("openclaw_responses"))
    db = _db_for_rule(_rule(canary_percent=0))

    result = await ProviderRuntimeRouter(db).route(_request())

    assert result.ok is True
    assert result.provider == "openclaw_responses"


@pytest.mark.asyncio
async def test_canary_full_routes_to_codex(monkeypatch):
    import app.services.provider_runtime as provider_runtime_module

    monkeypatch.setattr(provider_runtime_module, "bootstrap_provider_runtime", lambda: None)
    ProviderRegistry.register("codex_app_server", lambda db: SuccessAdapter("codex_app_server"))
    ProviderRegistry.register("openclaw_responses", lambda db: SuccessAdapter("openclaw_responses"))
    db = _db_for_rule(_rule(canary_percent=100))

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
        **_rule(canary_percent=100, kill_switch=True),
        "fallback_providers": '["openclaw_responses","rule_engine"]',
    })

    result = await ProviderRuntimeRouter(db).route(_request())

    assert result.ok is True
    assert result.provider == "openclaw_responses"


@pytest.mark.asyncio
async def test_canary_bucket_is_stable_across_request_ids(monkeypatch):
    import app.services.provider_runtime as provider_runtime_module

    monkeypatch.setattr(provider_runtime_module, "bootstrap_provider_runtime", lambda: None)
    ProviderRegistry.register("codex_app_server", lambda db: SuccessAdapter("codex_app_server"))
    ProviderRegistry.register("openclaw_responses", lambda db: SuccessAdapter("openclaw_responses"))
    session_id = _session_for_bucket(0)

    db_one = _db_for_rule(_rule(canary_percent=1))
    db_two = _db_for_rule(_rule(canary_percent=1))

    first = await ProviderRuntimeRouter(db_one).route(_request(session_id=session_id, request_id="req-a"))
    second = await ProviderRuntimeRouter(db_two).route(_request(session_id=session_id, request_id="req-b"))

    assert first.provider == "codex_app_server"
    assert second.provider == "codex_app_server"


def test_canary_bucket_zero_session_can_be_precomputed_for_one_percent_smoke():
    session_id = _session_for_bucket(0)

    assert ProviderRuntimeRouter._stable_percent_bucket("default", "website", session_id) == 0


@pytest.mark.asyncio
async def test_canary_one_percent_routes_bucket_zero_to_codex(monkeypatch):
    import app.services.provider_runtime as provider_runtime_module

    monkeypatch.setattr(provider_runtime_module, "bootstrap_provider_runtime", lambda: None)
    ProviderRegistry.register("codex_app_server", lambda db: SuccessAdapter("codex_app_server"))
    ProviderRegistry.register("openclaw_responses", lambda db: SuccessAdapter("openclaw_responses"))
    session_id = _session_for_bucket(0)
    db = _db_for_rule(_rule(canary_percent=1))

    result = await ProviderRuntimeRouter(db).route(_request(session_id=session_id))

    assert result.ok is True
    assert result.provider == "codex_app_server"


@pytest.mark.asyncio
async def test_canary_one_percent_routes_bucket_above_zero_to_fallback(monkeypatch):
    import app.services.provider_runtime as provider_runtime_module

    monkeypatch.setattr(provider_runtime_module, "bootstrap_provider_runtime", lambda: None)
    ProviderRegistry.register("codex_app_server", lambda db: SuccessAdapter("codex_app_server"))
    ProviderRegistry.register("openclaw_responses", lambda db: SuccessAdapter("openclaw_responses"))
    session_id = _session_above_bucket(1)
    db = _db_for_rule(_rule(canary_percent=1))

    result = await ProviderRuntimeRouter(db).route(_request(session_id=session_id))

    assert result.ok is True
    assert result.provider == "openclaw_responses"
