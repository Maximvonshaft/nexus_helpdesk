from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from app.services import provider_runtime as provider_runtime_module
from app.services.provider_runtime.registry import ProviderAdapter, ProviderRegistry
from app.services.provider_runtime.router import ProviderRuntimeRouter
from app.services.provider_runtime.traffic_selection import effective_kill_switch, safe_traffic_configuration
from app.services.provider_runtime.schemas import ProviderRequest, ProviderResult
from app.services.webcall_ai_production import orchestrator
from app.services.webcall_ai_production.providers.base import STTResult
from app.services.webcall_ai_production.providers import provider_runtime_llm as webcall_provider_module
from app.services.webcall_ai_production.providers.provider_runtime_llm import ProviderRuntimeLLMProvider
from app.api.admin_provider_runtime import WebchatRuntimeRoutingUpdate, _sanitize_provider_runtime_snapshot


class _DummyAdapter(ProviderAdapter):
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
                "customer_reply": "candidate response",
                "language": "en",
                "intent": "other",
                "handoff_required": False,
                "ticket_should_create": False,
            },
        )


class _FakeSession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
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


def _request() -> ProviderRequest:
    return ProviderRequest(
        request_id="review-regression-request",
        tenant_id="tenant-a",
        tenant_key="tenant-a",
        channel_key="webcall_ai",
        session_id="session-a",
        scenario="webcall_ai_decision",
        body="hello",
        output_contract="nexus_webchat_runtime_reply_v1",
        timeout_ms=1000,
    )


def _mock_db_with_rule(*, kill_switch: bool):
    db = Mock()
    select_result = Mock()
    select_result.mappings.return_value.first.return_value = {
        "primary_provider": "private_ai_runtime",
        "fallback_providers": [],
        "output_contract": "nexus_webchat_runtime_reply_v1",
        "timeout_ms": 1000,
        "kill_switch": kill_switch,
        "canary_percent": 100,
    }

    def execute(statement, params=None, *args, **kwargs):
        if "insert into provider_runtime_audit_logs" in str(statement).lower():
            return Mock()
        return select_result

    db.execute.side_effect = execute
    return db


def test_false_environment_default_preserves_persisted_kill_switch(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_KILL_SWITCH", "false")
    assert effective_kill_switch(True) is True
    assert effective_kill_switch(False) is False
    summary = safe_traffic_configuration(default_canary_percent=100, default_kill_switch=True)
    assert summary["kill_switch"] is True
    assert summary["kill_switch_env_override"] is True


@pytest.mark.parametrize("value", [True, False])
def test_admin_rejects_boolean_canary_percent(value):
    with pytest.raises(ValidationError):
        WebchatRuntimeRoutingUpdate(canary_percent=value)


def test_admin_snapshot_sanitizer_removes_exception_derived_text():
    secret = "postgresql://user:secret@private-host/runtime"
    sanitized = _sanitize_provider_runtime_snapshot({
        "ok": False,
        "config_error": f"RuntimeError: {secret}",
        "human_webcall": {"warnings": [f"human_webcall status unavailable: RuntimeError {secret}"]},
    })
    assert sanitized["config_error"] == "provider_runtime_settings_invalid"
    assert sanitized["human_webcall"]["warnings"] == ["human_webcall status unavailable"]
    assert secret not in repr(sanitized)


@pytest.mark.asyncio
async def test_false_environment_default_cannot_clear_persisted_router_kill_switch(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "canary")
    monkeypatch.setenv("PROVIDER_RUNTIME_CANARY_PERCENT", "100")
    monkeypatch.setenv("PROVIDER_RUNTIME_KILL_SWITCH", "false")
    adapter = _DummyAdapter()
    ProviderRegistry.register("private_ai_runtime", lambda db: adapter)

    result = await ProviderRuntimeRouter(_mock_db_with_rule(kill_switch=True)).route(_request())

    assert result.ok is False
    assert result.error_code == "kill_switch_active"
    assert adapter.calls == 0
    assert result.raw_payload_safe_summary["traffic_selection"]["path"] == "kill_switch"


@pytest.mark.parametrize("error_code", ["provider_canary_control_path", "provider_shadow_completed", "provider_shadow_failed"])
def test_webcall_non_authoritative_router_outcomes_are_neutral(monkeypatch, error_code):
    session = _FakeSession()
    monkeypatch.setattr(webcall_provider_module, "SessionLocal", lambda: session)

    async def neutral_route(db, request):
        return ProviderResult.unavailable("router", error_code, 0, fallback_allowed=False)

    monkeypatch.setattr(webcall_provider_module, "_route_request", neutral_route)

    result = ProviderRuntimeLLMProvider().respond("hello", language="en")

    assert result.response_text == ""
    assert result.intent == "provider_runtime_non_authoritative"
    assert result.handoff_required is False
    assert result.handoff_reason is None
    assert result.provider_name == f"provider_runtime:{error_code}"
    assert session.closed is True


def test_webcall_orchestrator_does_not_convert_control_outcome_to_handoff(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(webcall_provider_module, "SessionLocal", lambda: session)

    async def control_route(db, request):
        return ProviderResult.unavailable(
            "router",
            "provider_canary_control_path",
            0,
            fallback_allowed=False,
        )

    monkeypatch.setattr(webcall_provider_module, "_route_request", control_route)

    result = orchestrator._safe_llm_response(
        SimpleNamespace(llm_provider="provider_runtime"),
        STTResult(text="hello", language="en", provider_name="fake"),
    )

    assert result.intent == "provider_runtime_non_authoritative"
    assert result.handoff_required is False
    assert result.handoff_reason is None
