from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.settings import get_settings
from app.api.admin_provider_runtime import WebchatFastRoutingUpdate, update_webchat_fast_reply_routing
from app.services.provider_runtime.adapters.codex_direct import CodexDirectAdapter
from app.services.provider_runtime.registry import ProviderAdapter, ProviderRegistry
from app.services.provider_runtime.router import ProviderRuntimeRouter
from app.services.provider_runtime.schemas import ProviderRequest, ProviderResult


def _clear_settings() -> None:
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def clear_settings_cache():
    _clear_settings()
    yield
    _clear_settings()


def _configure_direct(monkeypatch, tmp_path, *, enabled: bool = True, command: str | None = None):
    home = tmp_path / "home"
    auth_dir = home / ".codex"
    auth_dir.mkdir(parents=True)
    (auth_dir / "auth.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CODEX_DIRECT_ENABLED", "true" if enabled else "false")
    monkeypatch.setenv("CODEX_DIRECT_COMMAND", command or sys.executable)
    monkeypatch.setenv("CODEX_DIRECT_HOME", str(home))
    monkeypatch.setenv("CODEX_DIRECT_MODEL", "test-codex-model")
    monkeypatch.setenv("CODEX_DIRECT_TIMEOUT_SECONDS", "25")
    monkeypatch.setenv("CODEX_DIRECT_MAX_PROMPT_CHARS", "12000")
    monkeypatch.setenv("CODEX_DIRECT_REQUIRE_JSON", "true")
    _clear_settings()
    return home


def _request(**overrides) -> ProviderRequest:
    data = {
        "request_id": "req1",
        "tenant_id": "default",
        "tenant_key": "default",
        "channel_key": "website",
        "session_id": "sess1",
        "scenario": "webchat_fast_reply",
        "body": "Please help me track my parcel.",
        "recent_context": [],
        "tracking_fact_summary": None,
        "tracking_fact_evidence_present": False,
        "output_contract": "speedaf_webchat_fast_reply_v1",
        "timeout_ms": 10000,
        "metadata": {},
    }
    data.update(overrides)
    return ProviderRequest(**data)


def _completed(stdout: str = "", *, returncode: int = 0, stderr: str = ""):
    return SimpleNamespace(args=["codex"], returncode=returncode, stdout=stdout, stderr=stderr)


class TimeoutExpired(Exception):
    pass


@pytest.mark.asyncio
async def test_codex_direct_disabled_returns_specific_error(monkeypatch, tmp_path):
    _configure_direct(monkeypatch, tmp_path, enabled=False)

    result = await CodexDirectAdapter().generate(Mock(), _request())

    assert result.ok is False
    assert result.error_code == "codex_direct_disabled"


@pytest.mark.asyncio
async def test_codex_direct_binary_missing_returns_specific_error(monkeypatch, tmp_path):
    _configure_direct(monkeypatch, tmp_path, command=str(tmp_path / "missing-codex"))

    result = await CodexDirectAdapter().generate(Mock(), _request())

    assert result.ok is False
    assert result.error_code == "codex_direct_binary_missing"


@pytest.mark.asyncio
async def test_codex_direct_auth_missing_returns_specific_error(monkeypatch, tmp_path):
    home = _configure_direct(monkeypatch, tmp_path)
    (home / ".codex" / "auth.json").unlink()

    result = await CodexDirectAdapter().generate(Mock(), _request())

    assert result.ok is False
    assert result.error_code == "codex_direct_auth_missing"


@pytest.mark.asyncio
async def test_codex_direct_not_logged_in_returns_specific_error(monkeypatch, tmp_path):
    _configure_direct(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "app.services.provider_runtime.adapters.codex_direct._run_subprocess",
        Mock(return_value=_completed("Not logged in")),
    )

    result = await CodexDirectAdapter().generate(Mock(), _request())

    assert result.ok is False
    assert result.error_code == "codex_direct_not_logged_in"


@pytest.mark.asyncio
async def test_codex_direct_timeout_returns_specific_error(monkeypatch, tmp_path):
    _configure_direct(monkeypatch, tmp_path)
    run = Mock(side_effect=[_completed("Logged in using ChatGPT"), TimeoutExpired()])
    monkeypatch.setattr("app.services.provider_runtime.adapters.codex_direct._run_subprocess", run)

    result = await CodexDirectAdapter().generate(Mock(), _request())

    assert result.ok is False
    assert result.error_code == "codex_direct_timeout"
    assert run.call_args.kwargs["shell"] is False


@pytest.mark.asyncio
async def test_codex_direct_nonzero_exit_returns_specific_error(monkeypatch, tmp_path):
    _configure_direct(monkeypatch, tmp_path)
    run = Mock(side_effect=[_completed("Logged in using ChatGPT"), _completed("", returncode=2, stderr="secret stderr")])
    monkeypatch.setattr("app.services.provider_runtime.adapters.codex_direct._run_subprocess", run)

    result = await CodexDirectAdapter().generate(Mock(), _request())

    assert result.ok is False
    assert result.error_code == "codex_direct_nonzero_exit"
    assert "secret stderr" not in str(result.raw_payload_safe_summary)


@pytest.mark.asyncio
async def test_codex_direct_invalid_json_returns_bad_json(monkeypatch, tmp_path):
    _configure_direct(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "app.services.provider_runtime.adapters.codex_direct._run_subprocess",
        Mock(side_effect=[_completed("Logged in using ChatGPT"), _completed("not json")]),
    )

    result = await CodexDirectAdapter().generate(Mock(), _request())

    assert result.ok is False
    assert result.error_code == "codex_direct_bad_json"


@pytest.mark.asyncio
async def test_codex_direct_empty_customer_reply_returns_specific_error(monkeypatch, tmp_path):
    _configure_direct(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "app.services.provider_runtime.adapters.codex_direct._run_subprocess",
        Mock(side_effect=[_completed("Logged in using ChatGPT"), _completed('{"customer_reply":"","language":"en","intent":"other","handoff_required":false,"ticket_should_create":false}')]),
    )

    result = await CodexDirectAdapter().generate(Mock(), _request())

    assert result.ok is False
    assert result.error_code == "codex_direct_empty_reply"


@pytest.mark.asyncio
async def test_codex_direct_successful_json_returns_provider_result(monkeypatch, tmp_path):
    home = _configure_direct(monkeypatch, tmp_path)
    run = Mock(
        side_effect=[
            _completed("Logged in using ChatGPT"),
            _completed(
                '{"customer_reply":"Please share your tracking number and I will check it.","language":"en","intent":"tracking_lookup",'
                '"handoff_required":false,"ticket_should_create":false,"tool_calls":[],"evidence_used":[],"confidence":0.9,"reason":"Need tracking number."}'
            ),
        ]
    )
    monkeypatch.setattr("app.services.provider_runtime.adapters.codex_direct._run_subprocess", run)

    result = await CodexDirectAdapter().generate(Mock(), _request())

    assert result.ok is True
    assert result.provider == "codex_direct"
    assert result.error_code is None
    assert result.structured_output["customer_reply"].startswith("Please share")
    assert result.structured_output["intent"] == "tracking_missing_number"
    generate_call = run.call_args_list[1]
    assert generate_call.kwargs["shell"] is False
    assert generate_call.kwargs["env"]["HOME"] == str(home)


def test_registry_resolves_codex_direct(monkeypatch):
    import app.services.provider_runtime as provider_runtime_module

    provider_runtime_module._BOOTSTRAPPED = False
    provider_runtime_module.bootstrap_provider_runtime()

    adapter = ProviderRegistry.get("codex_direct", Mock())

    assert isinstance(adapter, CodexDirectAdapter)


class SuccessAdapter(ProviderAdapter):
    name = "codex_direct"

    async def generate(self, db, request):
        return ProviderResult(
            ok=True,
            provider=self.name,
            elapsed_ms=5,
            structured_output={
                "customer_reply": "I can help with that.",
                "language": "en",
                "intent": "other",
                "handoff_required": False,
                "ticket_should_create": False,
            },
            raw_payload_safe_summary={"reply_source": self.name},
        )


class FailureAdapter(ProviderAdapter):
    def __init__(self, name: str, error_code: str):
        self.name = name
        self.error_code = error_code

    async def generate(self, db, request):
        return ProviderResult.unavailable(self.name, self.error_code, 5)


@pytest.mark.asyncio
async def test_router_selects_codex_direct_from_primary_provider_env(monkeypatch):
    import app.services.provider_runtime as provider_runtime_module

    monkeypatch.setenv("PROVIDER_RUNTIME_PRIMARY_PROVIDER", "codex_direct")
    _clear_settings()
    monkeypatch.setattr(provider_runtime_module, "bootstrap_provider_runtime", lambda: None)
    ProviderRegistry.register("codex_direct", lambda db: SuccessAdapter())
    db = Mock()
    select_result = Mock()
    select_result.mappings.return_value.first.return_value = None

    def execute(stmt, params=None, *args, **kwargs):
        if "INSERT INTO provider_runtime_audit_logs" in str(stmt):
            return Mock()
        return select_result

    db.execute.side_effect = execute

    result = await ProviderRuntimeRouter(db).route(_request())

    assert result.ok is True
    assert result.provider == "codex_direct"
    assert db.commit.called


@pytest.mark.asyncio
async def test_router_preserves_codex_direct_primary_error_code(monkeypatch):
    import app.services.provider_runtime as provider_runtime_module

    monkeypatch.setenv("PROVIDER_RUNTIME_PRIMARY_PROVIDER", "codex_direct")
    _clear_settings()
    monkeypatch.setattr(provider_runtime_module, "bootstrap_provider_runtime", lambda: None)
    ProviderRegistry.register("codex_direct", lambda db: FailureAdapter("codex_direct", "codex_direct_auth_missing"))
    ProviderRegistry.register("rule_engine", lambda db: FailureAdapter("rule_engine", "rule_engine_skeleton_unavailable"))
    db = Mock()
    select_result = Mock()
    select_result.mappings.return_value.first.return_value = None

    def execute(stmt, params=None, *args, **kwargs):
        if "INSERT INTO provider_runtime_audit_logs" in str(stmt):
            return Mock()
        return select_result

    db.execute.side_effect = execute

    result = await ProviderRuntimeRouter(db).route(_request())

    assert result.ok is False
    assert result.provider == "codex_direct"
    assert result.error_code == "codex_direct_auth_missing"


def test_codex_direct_smoke_returns_ready_without_secrets(monkeypatch, tmp_path):
    from app.api.admin_provider_credentials import codex_direct_smoke

    _configure_direct(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "app.services.provider_runtime.adapters.codex_direct._run_subprocess",
        Mock(return_value=_completed("Logged in using ChatGPT with token secret-token")),
    )
    monkeypatch.setattr("app.api.admin_provider_credentials._ensure_admin_manage", lambda current_user, db: None)

    response = codex_direct_smoke(db=Mock(), current_user=Mock())

    assert response["ready"] is True
    assert response["provider"] == "codex_direct"
    assert response["login_status"] == "logged_in"
    assert response["error_code"] is None
    assert "secret-token" not in str(response)


def test_codex_direct_smoke_returns_failure_without_secrets(monkeypatch, tmp_path):
    from app.api.admin_provider_credentials import codex_direct_smoke

    home = _configure_direct(monkeypatch, tmp_path)
    (home / ".codex" / "auth.json").unlink()
    monkeypatch.setattr("app.api.admin_provider_credentials._ensure_admin_manage", lambda current_user, db: None)

    response = codex_direct_smoke(db=Mock(), current_user=Mock())

    assert response["ready"] is False
    assert response["auth_file_exists"] is False
    assert response["error_code"] == "codex_direct_auth_missing"


def test_admin_routing_codex_direct_defaults_to_rule_engine_fallback(monkeypatch):
    monkeypatch.setattr("app.api.admin_provider_runtime.ensure_can_manage_runtime", lambda current_user, db: None)
    db = Mock()
    select_result = Mock()
    select_result.mappings.return_value.first.return_value = None
    db.execute.return_value = select_result

    response = update_webchat_fast_reply_routing(
        WebchatFastRoutingUpdate(primary_provider="codex_direct"),
        db=db,
        current_user=Mock(),
    )

    assert response["ok"] is True
    assert response["routing_rule"]["primary_provider"] == "codex_direct"
    assert response["routing_rule"]["fallback_providers"] == ["rule_engine"]
