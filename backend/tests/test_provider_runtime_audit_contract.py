import json
import uuid
from unittest.mock import Mock

import pytest

from app.services import provider_runtime as provider_runtime_module
from app.services.provider_runtime import router as router_module
from app.services.provider_runtime.registry import ProviderAdapter, ProviderRegistry
from app.services.provider_runtime.router import ProviderRuntimeRouter
from app.services.provider_runtime.schemas import ProviderRequest, ProviderResult
from app.services.provider_runtime.traffic_selection import stable_canary_bucket
from app.services.webcall_ai_production.providers import provider_runtime_llm as webcall_module
from app.services.webcall_ai_production.providers.provider_runtime_llm import ProviderRuntimeLLMProvider


class _Adapter(ProviderAdapter):
    def __init__(self, name: str, result: ProviderResult):
        self.name = name
        self.result = result
        self.calls = 0

    async def generate(self, db, request):
        self.calls += 1
        return self.result


class _FakeSession:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _isolated_runtime(monkeypatch):
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
        "WEBCALL_AI_PROVIDER_RUNTIME_PROVIDER",
        "WEBCALL_AI_PROVIDER_RUNTIME_SESSION_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    yield


def _request(*, request_id: str = "audit-contract-request") -> ProviderRequest:
    return ProviderRequest(
        request_id=request_id,
        tenant_id="tenant-a",
        tenant_key="tenant-a",
        channel_key="website",
        session_id="session-a",
        scenario="webchat_runtime_reply",
        body="hello",
        output_contract="nexus_webchat_runtime_reply_v1",
        timeout_ms=1000,
    )


def _valid_result(provider: str = "private_ai_runtime") -> ProviderResult:
    return ProviderResult(
        ok=True,
        provider=provider,
        elapsed_ms=1,
        structured_output={
            "customer_reply": "hello",
            "language": "en",
            "intent": "greeting",
            "handoff_required": False,
            "ticket_should_create": False,
        },
    )


def _rule(*, primary="private_ai_runtime", fallbacks=None, canary_percent=100):
    return {
        "primary_provider": primary,
        "fallback_providers": [] if fallbacks is None else fallbacks,
        "output_contract": "nexus_webchat_runtime_reply_v1",
        "timeout_ms": 1000,
        "kill_switch": False,
        "canary_percent": canary_percent,
    }


def _mock_db(rule):
    db = Mock()
    selected = Mock()
    selected.mappings.return_value.first.return_value = rule
    db.audit_rows = []

    def execute(statement, params=None, *args, **kwargs):
        if "insert into provider_runtime_audit_logs" in str(statement).lower():
            db.audit_rows.append(dict(params or {}))
            return Mock()
        return selected

    db.execute.side_effect = execute
    return db


def _summary(row):
    return json.loads(row["safe_summary"])


@pytest.mark.asyncio
async def test_persisted_null_canary_fails_closed_before_environment_override(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "canary")
    monkeypatch.setenv("PROVIDER_RUNTIME_CANARY_PERCENT", "100")
    db = _mock_db(_rule(canary_percent=None))
    adapter = _Adapter("private_ai_runtime", _valid_result())
    ProviderRegistry.register("private_ai_runtime", lambda session: adapter)

    result = await ProviderRuntimeRouter(db).route(_request())

    assert result.ok is False
    assert result.error_code == "provider_runtime_canary_percent_invalid"
    assert adapter.calls == 0
    assert _summary(db.audit_rows[-1])["fallback_result"] == "blocked"


@pytest.mark.asyncio
async def test_unsupported_persisted_primary_blocks_approved_fallback_before_any_adapter(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "canary")
    db = _mock_db(
        _rule(
            primary="customer-controlled-provider-alias",
            fallbacks='["private_ai_runtime"]',
            canary_percent=100,
        )
    )
    fallback = _Adapter("private_ai_runtime", _valid_result())
    ProviderRegistry.register("private_ai_runtime", lambda session: fallback)

    result = await ProviderRuntimeRouter(db).route(_request())

    assert result.ok is False
    assert result.error_code == "provider_runtime_provider_alias_invalid"
    assert fallback.calls == 0
    audit = db.audit_rows[-1]
    assert audit["provider"] == "router"
    summary = _summary(audit)
    assert summary["fallback_result"] == "blocked"
    assert "customer-controlled-provider-alias" not in repr(summary)


@pytest.mark.asyncio
async def test_control_audit_uses_fixed_fallback_result_enum(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "control")
    db = _mock_db(_rule(canary_percent=0))

    result = await ProviderRuntimeRouter(db).route(_request())

    assert result.error_code == "provider_canary_control_path"
    assert _summary(db.audit_rows[-1])["fallback_result"] == "not_attempted"


@pytest.mark.asyncio
async def test_primary_failure_then_fallback_success_is_audited_as_pending_then_succeeded(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "canary")
    backup_name = "private_ai_runtime_backup"
    monkeypatch.setattr(
        router_module,
        "_APPROVED_PROVIDER_ALIASES",
        frozenset({"private_ai_runtime", backup_name}),
    )
    db = _mock_db(_rule(fallbacks=json.dumps([backup_name])))
    marker = "PROVIDER-CONTROLLED-SUMMARY-MARKER"
    primary = _Adapter(
        "private_ai_runtime",
        ProviderResult(
            ok=False,
            provider="private_ai_runtime",
            elapsed_ms=2,
            error_code="customer_controlled_error_marker",
            fallback_allowed=True,
            raw_payload_safe_summary={"provider_body": marker},
        ),
    )
    backup = _Adapter(backup_name, _valid_result(backup_name))
    ProviderRegistry.register("private_ai_runtime", lambda session: primary)
    ProviderRegistry.register(backup_name, lambda session: backup)

    result = await ProviderRuntimeRouter(db).route(_request())

    assert result.ok is True
    assert primary.calls == 1
    assert backup.calls == 1
    provider_rows = [row for row in db.audit_rows if row["operation"] == "generate"]
    assert [_summary(row)["fallback_result"] for row in provider_rows] == [
        "pending",
        "succeeded",
    ]
    assert provider_rows[0]["error_code"] == "provider_runtime_provider_failed"
    assert marker not in repr(provider_rows)


@pytest.mark.asyncio
async def test_authoritative_nonfallback_failure_returns_router_owned_bounded_result(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "canary")
    marker = "CUSTOMER-OR-EXCEPTION-CONTROLLED-ERROR-MARKER"
    db = _mock_db(_rule(canary_percent=100))
    adapter = _Adapter(
        "private_ai_runtime",
        ProviderResult(
            ok=False,
            provider="private_ai_runtime",
            elapsed_ms=7,
            error_code=marker,
            fallback_allowed=False,
            raw_payload_safe_summary={"provider_body": marker},
        ),
    )
    ProviderRegistry.register("private_ai_runtime", lambda session: adapter)

    result = await ProviderRuntimeRouter(db).route(_request())

    assert result.ok is False
    assert result.provider == "router"
    assert result.raw_provider == "router"
    assert result.reply_source == "router"
    assert result.error_code == "provider_runtime_provider_failed"
    assert result.elapsed_ms == 7
    assert result.raw_payload_safe_summary["fallback_result"] == "blocked"
    assert result.raw_payload_safe_summary["provider_result"] == "failed"
    assert marker not in repr(result)
    assert adapter.calls == 1
    assert db.audit_rows[-1]["error_code"] == "provider_runtime_provider_failed"
    assert marker not in repr(db.audit_rows)


@pytest.mark.asyncio
async def test_shadow_failure_with_fallback_disallowed_never_calls_fallback(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "shadow")
    backup_name = "private_ai_runtime_backup"
    monkeypatch.setattr(
        router_module,
        "_APPROVED_PROVIDER_ALIASES",
        frozenset({"private_ai_runtime", backup_name}),
    )
    db = _mock_db(_rule(fallbacks=json.dumps([backup_name])))
    primary = _Adapter(
        "private_ai_runtime",
        ProviderResult.unavailable(
            "private_ai_runtime",
            "provider_timeout",
            3,
            fallback_allowed=False,
        ),
    )
    backup = _Adapter(backup_name, _valid_result(backup_name))
    ProviderRegistry.register("private_ai_runtime", lambda session: primary)
    ProviderRegistry.register(backup_name, lambda session: backup)

    result = await ProviderRuntimeRouter(db).route(_request())

    assert result.ok is False
    assert result.error_code == "provider_shadow_failed"
    assert primary.calls == 1
    assert backup.calls == 0
    provider_rows = [row for row in db.audit_rows if row["operation"] == "shadow_generate"]
    assert len(provider_rows) == 1
    assert _summary(provider_rows[0])["fallback_result"] == "blocked"


@pytest.mark.asyncio
async def test_unsupported_webcall_alias_writes_bounded_router_audit(monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_PROVIDER_RUNTIME_PROVIDER", "unsupported-provider-marker")
    db = _mock_db(None)
    request = webcall_module._build_request(text="hello", language="en")

    result = await webcall_module._route_request(db, request)

    assert result.ok is False
    assert result.error_code == "provider_runtime_provider_not_allowed"
    assert len(db.audit_rows) == 1
    audit = db.audit_rows[0]
    assert audit["provider"] == "router"
    assert audit["operation"] == "traffic_select"
    assert _summary(audit)["fallback_result"] == "blocked"
    assert "unsupported-provider-marker" not in repr(db.audit_rows)


def test_generic_webcall_requests_receive_distinct_server_generated_non_pii_identity(monkeypatch):
    captured_session_ids = []
    sessions = []

    def session_factory():
        session = _FakeSession()
        sessions.append(session)
        return session

    async def neutral_route(db, request):
        captured_session_ids.append(request.session_id)
        return ProviderResult.unavailable(
            "router",
            "provider_canary_control_path",
            0,
            fallback_allowed=False,
        )

    monkeypatch.setattr(webcall_module, "SessionLocal", session_factory)
    monkeypatch.setattr(webcall_module, "_route_request", neutral_route)

    provider = ProviderRuntimeLLMProvider()
    provider.respond("customer text marker", language="en")
    provider.respond("customer text marker", language="en")

    assert len(set(captured_session_ids)) == 2
    assert all(value.startswith("webcall-request-") for value in captured_session_ids)
    assert all("customer" not in value for value in captured_session_ids)
    assert all(session.closed for session in sessions)


def test_request_scoped_webcall_identity_distributes_documented_canary_stages(monkeypatch):
    tokens = iter(uuid.UUID(int=index) for index in range(1, 501))
    monkeypatch.setattr(webcall_module.uuid, "uuid4", lambda: next(tokens))

    requests = [
        webcall_module._build_request(text="same customer text", language="en")
        for _ in range(500)
    ]
    buckets = [stable_canary_bucket(request) for request in requests]

    assert len({request.session_id for request in requests}) == 500
    assert len(set(buckets)) >= 95
    for percent in (1, 5, 25):
        selected = sum(bucket < percent for bucket in buckets)
        assert 0 < selected < len(buckets)
