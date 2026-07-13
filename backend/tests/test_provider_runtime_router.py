import json
from unittest.mock import Mock

import pytest

from app.services import provider_runtime as provider_runtime_module
from app.services.provider_runtime.health import ProviderHealthDecision, ProviderRuntimeHealth
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
        "PROVIDER_RUNTIME_PRIMARY_PROVIDER",
        "PROVIDER_RUNTIME_FALLBACK_PROVIDERS",
        "PROVIDER_RUNTIME_OUTPUT_CONTRACT",
        "PROVIDER_RUNTIME_TIMEOUT_MS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "canary")
    yield


def _rule(*, canary_percent=100, kill_switch=False):
    return {
        "primary_provider": "private_ai_runtime",
        "fallback_providers": [],
        "output_contract": "nexus_webchat_runtime_reply_v1",
        "timeout_ms": 3000,
        "kill_switch": kill_switch,
        "canary_percent": canary_percent,
    }


def _mock_db(rule):
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


def _request(*, session_id: str = "s1") -> ProviderRequest:
    return ProviderRequest(
        request_id="req1",
        tenant_id="t1",
        tenant_key="tk1",
        channel_key="c1",
        session_id=session_id,
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


def _audit_summary(row) -> dict:
    return json.loads(row["safe_summary"])


@pytest.mark.asyncio
async def test_missing_rule_and_missing_traffic_env_default_to_control_zero(monkeypatch):
    monkeypatch.delenv("PROVIDER_RUNTIME_TRAFFIC_MODE", raising=False)
    monkeypatch.delenv("PROVIDER_RUNTIME_CANARY_PERCENT", raising=False)
    mock_db = _mock_db(None)
    adapter = DummyAdapter("private_ai_runtime", _valid_result())
    ProviderRegistry.register("private_ai_runtime", lambda db: adapter)

    result = await ProviderRuntimeRouter(mock_db).route(_request())

    assert result.error_code == "provider_canary_control_path"
    assert adapter.calls == 0
    traffic = _audit_summary(mock_db.audit_rows[-1])["traffic_selection"]
    assert traffic["configured_mode"] == "control"
    assert traffic["canary_percent"] == 0
    assert traffic["path"] == "control"


@pytest.mark.asyncio
async def test_provider_runtime_router_single_runtime_success_and_audit():
    mock_db = _mock_db(_rule())
    adapter = DummyAdapter("private_ai_runtime", _valid_result())
    ProviderRegistry.register("private_ai_runtime", lambda db: adapter)

    result = await ProviderRuntimeRouter(mock_db).route(_request())

    assert result.ok
    assert result.provider == "private_ai_runtime"
    assert result.structured_output["customer_reply"] == "hi"
    assert adapter.calls == 1
    assert mock_db.execute.call_count == 2
    summary = _audit_summary(mock_db.audit_rows[-1])
    assert summary["traffic_selection"]["path"] == "canary_authoritative"
    assert summary["traffic_selection"]["authoritative"] is True


@pytest.mark.asyncio
async def test_zero_percent_routes_to_control_without_calling_candidate():
    mock_db = _mock_db(_rule(canary_percent=0))
    adapter = DummyAdapter("private_ai_runtime", _valid_result())
    ProviderRegistry.register("private_ai_runtime", lambda db: adapter)

    result = await ProviderRuntimeRouter(mock_db).route(_request())

    assert not result.ok
    assert result.error_code == "provider_canary_control_path"
    assert adapter.calls == 0
    summary = _audit_summary(mock_db.audit_rows[-1])
    assert summary["traffic_selection"]["path"] == "control"
    assert summary["traffic_selection"]["execute_candidate"] is False


@pytest.mark.asyncio
async def test_environment_zero_percent_overrides_valid_database_rule(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_CANARY_PERCENT", "0")
    mock_db = _mock_db(_rule(canary_percent=100))
    adapter = DummyAdapter("private_ai_runtime", _valid_result())
    ProviderRegistry.register("private_ai_runtime", lambda db: adapter)

    result = await ProviderRuntimeRouter(mock_db).route(_request())

    assert result.error_code == "provider_canary_control_path"
    assert adapter.calls == 0


@pytest.mark.asyncio
async def test_shadow_mode_calls_candidate_but_never_returns_customer_output(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "shadow")
    mock_db = _mock_db(_rule())
    adapter = DummyAdapter("private_ai_runtime", _valid_result())
    ProviderRegistry.register("private_ai_runtime", lambda db: adapter)

    result = await ProviderRuntimeRouter(mock_db).route(_request())

    assert not result.ok
    assert result.error_code == "provider_shadow_completed"
    assert result.structured_output is None
    assert adapter.calls == 1
    assert mock_db.audit_rows[-1]["operation"] == "shadow_generate"
    assert mock_db.audit_rows[-1]["status"] == "ok"
    summary = _audit_summary(mock_db.audit_rows[-1])
    assert summary["traffic_selection"]["authoritative"] is False


@pytest.mark.asyncio
async def test_kill_switch_overrides_full_canary_and_shadow(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "shadow")
    mock_db = _mock_db(_rule(kill_switch=True))
    adapter = DummyAdapter("private_ai_runtime", _valid_result())
    ProviderRegistry.register("private_ai_runtime", lambda db: adapter)

    result = await ProviderRuntimeRouter(mock_db).route(_request())

    assert result.error_code == "kill_switch_active"
    assert adapter.calls == 0
    summary = _audit_summary(mock_db.audit_rows[-1])
    assert summary["traffic_selection"]["path"] == "kill_switch"


@pytest.mark.asyncio
async def test_valid_emergency_kill_switch_overrides_invalid_lower_priority_settings(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_KILL_SWITCH", "true")
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "invalid")
    monkeypatch.setenv("PROVIDER_RUNTIME_CANARY_PERCENT", "invalid")
    mock_db = _mock_db(_rule())
    adapter = DummyAdapter("private_ai_runtime", _valid_result())
    ProviderRegistry.register("private_ai_runtime", lambda db: adapter)

    result = await ProviderRuntimeRouter(mock_db).route(_request())

    assert result.error_code == "kill_switch_active"
    assert adapter.calls == 0
    audit = mock_db.audit_rows[-1]
    assert audit["operation"] == "traffic_select"
    assert audit["status"] == "skipped"
    summary = _audit_summary(audit)["traffic_selection"]
    assert summary["path"] == "kill_switch"
    assert summary["execute_candidate"] is False
    assert summary["authoritative"] is False
    assert summary["configuration_errors"] == [
        "provider_runtime_canary_percent_invalid",
        "provider_runtime_traffic_mode_invalid",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("environment", "value", "expected_error"),
    [
        ("PROVIDER_RUNTIME_TRAFFIC_MODE", "invalid", "provider_runtime_traffic_mode_invalid"),
        ("PROVIDER_RUNTIME_CANARY_PERCENT", "invalid", "provider_runtime_canary_percent_invalid"),
        ("PROVIDER_RUNTIME_KILL_SWITCH", "invalid", "provider_runtime_kill_switch_invalid"),
    ],
)
async def test_invalid_traffic_configuration_fails_closed_without_provider_call(
    monkeypatch,
    environment,
    value,
    expected_error,
):
    monkeypatch.setenv(environment, value)
    mock_db = _mock_db(_rule())
    adapter = DummyAdapter("private_ai_runtime", _valid_result())
    ProviderRegistry.register("private_ai_runtime", lambda db: adapter)

    result = await ProviderRuntimeRouter(mock_db).route(_request())

    assert result.error_code == expected_error
    assert adapter.calls == 0
    audit = mock_db.audit_rows[-1]
    assert audit["operation"] == "traffic_select"
    assert audit["status"] == "failed"
    assert audit["error_code"] == expected_error
    summary = _audit_summary(audit)
    assert summary["traffic_selection"]["configuration_errors"] == [expected_error]
    assert summary["traffic_selection"]["execute_candidate"] is False


@pytest.mark.asyncio
async def test_valid_env_canary_override_does_not_mask_invalid_persisted_canary(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_CANARY_PERCENT", "5")
    mock_db = _mock_db(_rule(canary_percent=101))
    adapter = DummyAdapter("private_ai_runtime", _valid_result())
    ProviderRegistry.register("private_ai_runtime", lambda db: adapter)

    result = await ProviderRuntimeRouter(mock_db).route(_request())

    assert result.error_code == "provider_runtime_canary_percent_invalid"
    assert adapter.calls == 0
    audit = mock_db.audit_rows[-1]
    assert audit["operation"] == "traffic_select"
    assert audit["status"] == "failed"
    assert _audit_summary(audit)["traffic_selection"]["configuration_errors"] == [
        "provider_runtime_canary_percent_invalid"
    ]


@pytest.mark.asyncio
async def test_valid_env_kill_false_does_not_mask_invalid_persisted_kill_switch(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_KILL_SWITCH", "false")
    mock_db = _mock_db(_rule(kill_switch="false"))
    adapter = DummyAdapter("private_ai_runtime", _valid_result())
    ProviderRegistry.register("private_ai_runtime", lambda db: adapter)

    result = await ProviderRuntimeRouter(mock_db).route(_request())

    assert result.error_code == "provider_runtime_kill_switch_invalid"
    assert adapter.calls == 0
    assert _audit_summary(mock_db.audit_rows[-1])["traffic_selection"]["execute_candidate"] is False


@pytest.mark.asyncio
async def test_valid_env_kill_true_overrides_invalid_persisted_values_and_records_drift(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_KILL_SWITCH", "true")
    monkeypatch.setenv("PROVIDER_RUNTIME_CANARY_PERCENT", "5")
    mock_db = _mock_db(_rule(canary_percent=101, kill_switch="false"))
    adapter = DummyAdapter("private_ai_runtime", _valid_result())
    ProviderRegistry.register("private_ai_runtime", lambda db: adapter)

    result = await ProviderRuntimeRouter(mock_db).route(_request())

    assert result.error_code == "kill_switch_active"
    assert adapter.calls == 0
    traffic = _audit_summary(mock_db.audit_rows[-1])["traffic_selection"]
    assert traffic["path"] == "kill_switch"
    assert traffic["configuration_errors"] == [
        "provider_runtime_canary_percent_invalid",
        "provider_runtime_kill_switch_invalid",
    ]


@pytest.mark.asyncio
async def test_health_skip_never_calls_candidate_and_every_audit_row_has_traffic_evidence(monkeypatch):
    monkeypatch.setattr(
        ProviderRuntimeHealth,
        "should_skip",
        lambda provider: ProviderHealthDecision(skip=True, reason="provider_health_cooldown", consecutive_failures=3),
    )
    mock_db = _mock_db(_rule())
    adapter = DummyAdapter("private_ai_runtime", _valid_result())
    ProviderRegistry.register("private_ai_runtime", lambda db: adapter)

    result = await ProviderRuntimeRouter(mock_db).route(_request())

    assert result.error_code == "all_providers_failed"
    assert adapter.calls == 0
    assert [row["status"] for row in mock_db.audit_rows] == ["skipped", "failed"]
    for row in mock_db.audit_rows:
        traffic = _audit_summary(row)["traffic_selection"]
        assert traffic["path"] == "canary_authoritative"
        assert traffic["authoritative"] is True


@pytest.mark.asyncio
async def test_authoritative_timeout_is_audited_and_fails_without_unapproved_fallback():
    timeout_result = ProviderResult.unavailable(
        "private_ai_runtime",
        "provider_timeout",
        3000,
        fallback_allowed=False,
    )
    mock_db = _mock_db(_rule())
    adapter = DummyAdapter("private_ai_runtime", timeout_result)
    ProviderRegistry.register("private_ai_runtime", lambda db: adapter)

    result = await ProviderRuntimeRouter(mock_db).route(_request())

    assert result.error_code == "provider_timeout"
    assert adapter.calls == 1
    audit = mock_db.audit_rows[-1]
    assert audit["operation"] == "generate"
    assert audit["status"] == "failed"
    traffic = _audit_summary(audit)["traffic_selection"]
    assert traffic["path"] == "canary_authoritative"
    assert traffic["authoritative"] is True


@pytest.mark.asyncio
async def test_provider_runtime_router_parse_reject_returns_no_customer_reply_or_exception_text():
    sensitive_upstream_value = "SENSITIVE-UPSTREAM-CUSTOMER-TEXT-DO-NOT-PERSIST"
    mock_db = _mock_db(_rule())
    adapter = DummyAdapter(
        "private_ai_runtime",
        ProviderResult(
            ok=True,
            provider="private_ai_runtime",
            elapsed_ms=100,
            structured_output={"customer_reply": sensitive_upstream_value},
        ),
    )
    ProviderRegistry.register("private_ai_runtime", lambda db: adapter)

    result = await ProviderRuntimeRouter(mock_db).route(_request())

    assert not result.ok
    assert result.error_code == "all_providers_failed"
    assert mock_db.execute.call_count == 3
    serialized_audit = json.dumps(mock_db.audit_rows, default=str)
    assert sensitive_upstream_value not in serialized_audit
    parse_reject_rows = [row for row in mock_db.audit_rows if row["operation"] == "parse_reject"]
    assert len(parse_reject_rows) == 1
    parse_summary = _audit_summary(parse_reject_rows[0])
    assert parse_summary["parse_reject"] is True
    assert parse_summary["parse_error_code"] == "provider_output_contract_rejected"
    assert "parse_error" not in parse_summary
    for row in mock_db.audit_rows:
        assert "traffic_selection" in _audit_summary(row)


@pytest.mark.asyncio
async def test_provider_runtime_router_accepts_trusted_tracking_followup_with_unrelated_locked_fact():
    mock_db = _mock_db(_rule())
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


def test_application_import_remains_available_for_route_smoke():
    from app.main import app

    assert app is not None
