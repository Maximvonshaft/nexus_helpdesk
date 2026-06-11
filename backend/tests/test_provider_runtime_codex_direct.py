from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.services.provider_runtime import bootstrap_provider_runtime
from app.services.provider_runtime.adapters.codex_direct import CodexDirectAdapter, _runtime_timeout_seconds
from app.services.provider_runtime.registry import ProviderAdapter, ProviderRegistry
from app.services.provider_runtime.router import ProviderRuntimeRouter
from app.services.provider_runtime.schemas import ProviderRequest, ProviderResult


@pytest.fixture()
def fake_codex(tmp_path: Path):
    script = tmp_path / "codex"
    script.write_text(
        """
#!/usr/bin/env python3
import json
import os
import sys
import time

args = sys.argv[1:]
if args == ["login", "status"]:
    print(os.environ.get("CODEX_FAKE_LOGIN_STATUS", "Logged in using ChatGPT"))
    raise SystemExit(int(os.environ.get("CODEX_FAKE_LOGIN_EXIT", "0")))

sleep_seconds = float(os.environ.get("CODEX_FAKE_SLEEP", "0") or "0")
if sleep_seconds:
    time.sleep(sleep_seconds)

exit_code = int(os.environ.get("CODEX_FAKE_EXIT", "0") or "0")
if exit_code:
    print("nonzero stderr", file=sys.stderr)
    raise SystemExit(exit_code)

mode = os.environ.get("CODEX_FAKE_MODE", "success")
if mode == "empty":
    raise SystemExit(0)
if mode == "bad_json":
    print("this is not json")
    raise SystemExit(0)

stdin_payload = sys.stdin.read()
print(json.dumps({
    "customer_reply": "Sure. Please send me your tracking number and I will check it for you.",
    "language": "en",
    "intent": "tracking_lookup",
    "handoff_required": False,
    "ticket_should_create": False,
    "tool_calls": [
        {"tool_name": "speedaf.track", "arguments": {"tracking_number": "SF123456789"}},
        {"tool_name": "speedaf.cancel_order", "arguments": {"tracking_number": "SF123456789"}}
    ],
    "evidence_used": [{"source":"knowledge_base", "source_id":"kb_1", "snippet":"Ask for tracking number."}],
    "confidence": 0.86,
    "reason": "User wants parcel tracking.",
    "stdin_used": bool(stdin_payload)
}))
""".lstrip(),
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


@pytest.fixture()
def codex_home(tmp_path: Path):
    home = tmp_path / "home"
    auth_dir = home / ".codex"
    auth_dir.mkdir(parents=True)
    (auth_dir / "auth.json").write_text('{"status":"test"}', encoding="utf-8")
    return home


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    keys = {
        "APP_ENV",
        "ENV",
        "CODEX_DIRECT_ENABLED",
        "CODEX_DIRECT_COMMAND",
        "CODEX_DIRECT_HOME",
        "CODEX_DIRECT_MODEL",
        "CODEX_DIRECT_TIMEOUT_SECONDS",
        "CODEX_DIRECT_MAX_PROMPT_CHARS",
        "CODEX_DIRECT_REQUIRE_JSON",
        "CODEX_DIRECT_EXEC_ARGS_TEMPLATE",
        "CODEX_DIRECT_SANDBOX_ACKNOWLEDGED",
        "CODEX_DIRECT_ALLOW_NETWORK_ENV",
        "CODEX_DIRECT_FALLBACK_ALLOWED",
        "CODEX_DIRECT_READINESS_CACHE_SECONDS",
        "CODEX_FAKE_LOGIN_STATUS",
        "CODEX_FAKE_LOGIN_EXIT",
        "CODEX_FAKE_SLEEP",
        "CODEX_FAKE_EXIT",
        "CODEX_FAKE_MODE",
        "PROVIDER_RUNTIME_PRIMARY_PROVIDER",
        "PROVIDER_RUNTIME_FALLBACK_PROVIDERS",
        "PROVIDER_RUNTIME_TIMEOUT_MS",
        "PROVIDER_RUNTIME_OUTPUT_CONTRACT",
        "PROVIDER_RUNTIME_KILL_SWITCH",
        "WEBCHAT_FAST_AI_FALLBACK_PROVIDER",
        "DATABASE_URL",
        "INTERNAL_SERVICE_PASSWORD",
    }
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("APP_ENV", "test")
    CodexDirectAdapter._readiness_cache.clear()


def _enable_direct(monkeypatch, *, fake_codex: Path, home: Path, timeout: int = 5, fallback_allowed: bool = True):
    monkeypatch.setenv("CODEX_DIRECT_ENABLED", "true")
    monkeypatch.setenv("CODEX_DIRECT_COMMAND", f'"{sys.executable}" "{fake_codex}"')
    monkeypatch.setenv("CODEX_DIRECT_HOME", str(home))
    monkeypatch.setenv("CODEX_DIRECT_MODEL", "test-codex-model")
    monkeypatch.setenv("CODEX_DIRECT_TIMEOUT_SECONDS", str(timeout))
    monkeypatch.setenv("CODEX_DIRECT_EXEC_ARGS_TEMPLATE", "exec --model {model} -")
    monkeypatch.setenv("CODEX_DIRECT_FALLBACK_ALLOWED", "true" if fallback_allowed else "false")


def _request(**overrides) -> ProviderRequest:
    data = {
        "request_id": "req-1",
        "tenant_id": "default",
        "tenant_key": "default",
        "channel_key": "website",
        "session_id": "sess-1",
        "scenario": "webchat_fast_reply",
        "body": "Please help me track my parcel.",
        "recent_context": [],
        "tracking_fact_summary": None,
        "tracking_fact_evidence_present": False,
        "output_contract": "speedaf_webchat_fast_reply_v1",
        "timeout_ms": 5000,
        "metadata": {
            "context_version": "nexus_webchat_runtime_context_v2",
            "knowledge_context": {"hits": [], "locked_facts": [], "evidence_pack": []},
        },
    }
    data.update(overrides)
    return ProviderRequest(**data)


@pytest.mark.asyncio
async def test_codex_direct_disabled():
    res = await CodexDirectAdapter().generate(Mock(), _request())
    assert not res.ok
    assert res.error_code == "codex_direct_disabled"
    assert res.fallback_allowed is True


@pytest.mark.asyncio
async def test_codex_direct_production_requires_sandbox_ack(monkeypatch, fake_codex, codex_home):
    _enable_direct(monkeypatch, fake_codex=fake_codex, home=codex_home)
    monkeypatch.setenv("APP_ENV", "production")
    res = await CodexDirectAdapter().generate(Mock(), _request())
    assert not res.ok
    assert res.error_code == "codex_direct_sandbox_not_acknowledged"


@pytest.mark.asyncio
async def test_codex_direct_binary_missing(monkeypatch, codex_home):
    monkeypatch.setenv("CODEX_DIRECT_ENABLED", "true")
    monkeypatch.setenv("CODEX_DIRECT_COMMAND", str(codex_home / "missing-codex"))
    monkeypatch.setenv("CODEX_DIRECT_HOME", str(codex_home))
    res = await CodexDirectAdapter().generate(Mock(), _request())
    assert not res.ok
    assert res.error_code == "codex_direct_binary_missing"


@pytest.mark.asyncio
async def test_codex_direct_auth_missing(monkeypatch, fake_codex, tmp_path):
    home = tmp_path / "home-no-auth"
    home.mkdir()
    _enable_direct(monkeypatch, fake_codex=fake_codex, home=home)
    res = await CodexDirectAdapter().generate(Mock(), _request())
    assert not res.ok
    assert res.error_code == "codex_direct_auth_missing"


@pytest.mark.asyncio
async def test_codex_direct_not_logged_in(monkeypatch, fake_codex, codex_home):
    _enable_direct(monkeypatch, fake_codex=fake_codex, home=codex_home)
    monkeypatch.setenv("CODEX_FAKE_LOGIN_STATUS", "Not logged in")
    res = await CodexDirectAdapter().generate(Mock(), _request())
    assert not res.ok
    assert res.error_code == "codex_direct_not_logged_in"


@pytest.mark.asyncio
async def test_codex_direct_timeout(monkeypatch, fake_codex, codex_home):
    _enable_direct(monkeypatch, fake_codex=fake_codex, home=codex_home, timeout=1)
    monkeypatch.setenv("CODEX_FAKE_SLEEP", "3")
    res = await CodexDirectAdapter().generate(Mock(), _request())
    assert not res.ok
    assert res.error_code == "codex_direct_timeout"
    assert res.retryable is True


@pytest.mark.asyncio
async def test_codex_direct_nonzero_exit_safe_summary(monkeypatch, fake_codex, codex_home):
    _enable_direct(monkeypatch, fake_codex=fake_codex, home=codex_home)
    monkeypatch.setenv("CODEX_FAKE_EXIT", "2")
    res = await CodexDirectAdapter().generate(Mock(), _request())
    assert not res.ok
    assert res.error_code == "codex_direct_nonzero_exit"
    safe_blob = json.dumps(res.raw_payload_safe_summary).lower()
    assert "auth.json" not in safe_blob
    assert "internal_service_password" not in safe_blob
    assert "nonzero stderr" not in safe_blob


@pytest.mark.asyncio
async def test_codex_direct_bad_json(monkeypatch, fake_codex, codex_home):
    _enable_direct(monkeypatch, fake_codex=fake_codex, home=codex_home)
    monkeypatch.setenv("CODEX_FAKE_MODE", "bad_json")
    res = await CodexDirectAdapter().generate(Mock(), _request())
    assert not res.ok
    assert res.error_code == "codex_direct_bad_json"


@pytest.mark.asyncio
async def test_codex_direct_empty_reply(monkeypatch, fake_codex, codex_home):
    _enable_direct(monkeypatch, fake_codex=fake_codex, home=codex_home)
    monkeypatch.setenv("CODEX_FAKE_MODE", "empty")
    res = await CodexDirectAdapter().generate(Mock(), _request())
    assert not res.ok
    assert res.error_code == "codex_direct_empty_reply"


@pytest.mark.asyncio
async def test_codex_direct_success_normalizes_json_and_tool_allowlist(monkeypatch, fake_codex, codex_home):
    _enable_direct(monkeypatch, fake_codex=fake_codex, home=codex_home)
    res = await CodexDirectAdapter().generate(Mock(), _request())
    assert res.ok
    assert res.provider == "codex_direct"
    assert res.raw_provider == "codex_direct"
    assert res.reply_source == "codex_direct"
    assert res.structured_output["customer_reply"].startswith("Sure.")
    assert res.structured_output["intent"] == "tracking"
    assert res.structured_output["tool_calls"] == [
        {"tool_name": "speedaf.order.query", "arguments": {"tracking_number": "SF123456789"}, "idempotency_key": None, "reason": None, "requires_confirmation": False}
    ]
    assert res.structured_output["evidence_used"][0]["source"] == "knowledge_base"
    assert res.raw_payload_safe_summary["subprocess_mode"] == "to_thread_shell_false_stdin"
    assert res.raw_payload_safe_summary["readiness_cache_hit"] is False
    assert res.raw_payload_safe_summary["readiness_cache_ttl_seconds"] == 30
    assert res.raw_payload_safe_summary["auth_mtime_present"] is True
    assert isinstance(res.raw_payload_safe_summary["readiness_ms"], int)


@pytest.mark.asyncio
async def test_codex_direct_subprocess_env_is_scrubbed_and_codex_home_points_to_config_dir(monkeypatch, fake_codex, codex_home):
    _enable_direct(monkeypatch, fake_codex=fake_codex, home=codex_home)
    monkeypatch.setenv("DATABASE_URL", "postgresql://sensitive-db")
    monkeypatch.setenv("INTERNAL_SERVICE_PASSWORD", "sensitive-password")
    adapter = CodexDirectAdapter()
    env = adapter._subprocess_env()
    assert env["HOME"] == str(codex_home)
    assert env["CODEX_HOME"] == str(codex_home / ".codex")
    assert "DATABASE_URL" not in env
    assert "INTERNAL_SERVICE_PASSWORD" not in env


def test_codex_direct_runtime_timeout_preserves_subsecond_budget():
    assert _runtime_timeout_seconds(500, 25) == 0.5
    assert _runtime_timeout_seconds(10000, 25) == 10.0
    assert _runtime_timeout_seconds(None, 25) == 25.0
    assert _runtime_timeout_seconds(30000, 25) == 25.0


def test_codex_direct_prompt_supports_tracking_no_evidence_kb_guidance():
    adapter = CodexDirectAdapter()
    prompt = adapter._build_prompt(_request(
        body="CH1200000011425",
        tracking_fact_evidence_present=False,
        metadata={
            "context_version": "nexus_webchat_runtime_context_v2",
            "tracking_fact_metadata": {
                "fact_evidence_present": False,
                "tool_status": "failed",
                "tracking_fact_failure_reason": "1140003",
                "tracking_number_hash": "sha256:test",
            },
            "knowledge_context": {
                "retrieval_query": "CH1200000011425 运单号格式 wrong tracking number",
                "query_expansion_terms": ["运单号格式", "wrong tracking number"],
                "hits": [
                {
                    "item_key": "ch.waybill.format",
                    "title": "瑞士 Speedaf 运单号格式与输错提醒",
                    "score_breakdown": {"semantic": 100, "keyword": 42},
                    "matched_terms": ["CH1200000011425", "运单号格式", "wrong", "tracking", "number"] * 20,
                    "text": "Question: 客户输入瑞士 Speedaf 运单号查不到怎么办？ Answer: 请客户核对 CH 开头后接 12 位数字的完整运单号。",
                    "metadata": {"knowledge_kind": "business_fact", "fact_status": "approved", "answer_mode": "guided_answer"},
                    "source_metadata": {
                        "knowledge_kind": "business_fact",
                        "fact_status": "approved",
                        "answer_mode": "guided_answer",
                        "published_version": 7,
                        "large_internal_blob": "x" * 2000,
                    },
                    }
                ],
                "locked_facts": [],
                "evidence_pack": [{"item_key": "ch.waybill.format", "published_version": 1, "duplicate": "x" * 1000}],
                "injected_knowledge": [{"item_key": "ch.waybill.format", "duplicate": "x" * 1000}],
                "fallback_ngrams": ["x" * 50] * 50,
            },
        },
    ))

    assert "dynamically explain" in prompt
    assert "do not use canned wording" in prompt
    assert "Never include the raw customer-provided tracking/waybill number" in prompt
    assert "tracking_number=null" in prompt
    assert "ending 011425" in prompt
    assert "tracking_fact_metadata" in prompt
    assert "tracking_fact_failure_reason" in prompt
    assert "ch.waybill.format" in prompt
    assert "CH1200000011425" in prompt
    assert "score_breakdown" not in prompt
    assert "fallback_ngrams" not in prompt
    assert "matched_terms" not in prompt
    assert "evidence_pack" not in prompt
    assert "injected_knowledge" not in prompt
    assert "large_internal_blob" not in prompt
    assert len(prompt) < 5500


def test_codex_direct_no_evidence_tracking_output_suppresses_raw_identifier():
    normalized = CodexDirectAdapter()._normalize_output({
        "customer_reply": "I could not find a trusted live record for the waybill number you provided. Please verify that it uses the CH + 12 digit format and resend it if needed.",
        "language": "en",
        "intent": "tracking_unresolved",
        "tracking_number": None,
        "handoff_required": False,
        "ticket_should_create": False,
        "tool_calls": [],
        "evidence_used": [{"source":"hybrid_rag_v2", "evidence_type":"knowledge_context", "evidence_id":"ch.waybill.format", "fact_evidence_present": True, "raw_tracking_number_exposed": False}],
        "confidence": 0.82,
        "reason": "No trusted tracking fact is present; knowledge context contains format guidance.",
        "risk_level": "low",
        "next_action": "reply",
        "safety_notes": ["No live status claimed."],
    })

    reply = normalized["customer_reply"]
    assert "CH1200000011425" not in reply
    assert "1200000011425" not in reply
    assert "CH + 12 digit" in reply
    assert "verify" in reply.lower()
    assert normalized["intent"] == "tracking_unresolved"
    assert normalized["tracking_number"] is None
    forbidden = ("delivered", "in transit", "out for delivery", "customs", "returned", "签收", "运输中", "派送中", "清关", "退回")
    assert not any(term in reply.lower() for term in forbidden)


@pytest.mark.asyncio
async def test_codex_direct_smoke_ready(monkeypatch, fake_codex, codex_home):
    _enable_direct(monkeypatch, fake_codex=fake_codex, home=codex_home)
    smoke = await CodexDirectAdapter().smoke_check()
    assert smoke["ready"] is True
    assert smoke["error_code"] is None
    assert "auth.json" in smoke["checks"]["auth_path"]
    assert smoke["checks"]["codex_home"] == str(codex_home / ".codex")


@pytest.mark.asyncio
async def test_codex_direct_readiness_cache_reuses_success(monkeypatch, fake_codex, codex_home):
    _enable_direct(monkeypatch, fake_codex=fake_codex, home=codex_home)
    adapter = CodexDirectAdapter()
    calls = []

    def fake_run(argv, input_text, timeout_seconds):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="Logged in using ChatGPT", stderr="")

    monkeypatch.setattr(adapter, "_run_sync", fake_run)
    first = await adapter.readiness_check()
    second = await adapter.readiness_check()

    assert first.ready is True
    assert second.ready is True
    assert first.safe_summary["readiness_cache_hit"] is False
    assert second.safe_summary["readiness_cache_hit"] is True
    assert second.safe_summary["readiness_cache_ttl_seconds"] == 30
    assert second.safe_summary["auth_mtime_present"] is True
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_codex_direct_readiness_cache_invalidates_on_auth_metadata_change(monkeypatch, fake_codex, codex_home):
    _enable_direct(monkeypatch, fake_codex=fake_codex, home=codex_home)
    adapter = CodexDirectAdapter()
    calls = []

    def fake_run(argv, input_text, timeout_seconds):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="Logged in using ChatGPT", stderr="")

    monkeypatch.setattr(adapter, "_run_sync", fake_run)
    assert (await adapter.readiness_check()).ready is True
    (codex_home / ".codex" / "auth.json").write_text('{"status":"changed","extra":true}', encoding="utf-8")
    second = await adapter.readiness_check()

    assert second.ready is True
    assert second.safe_summary["readiness_cache_hit"] is False
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_codex_direct_failed_readiness_is_not_cached(monkeypatch, fake_codex, codex_home):
    _enable_direct(monkeypatch, fake_codex=fake_codex, home=codex_home)
    adapter = CodexDirectAdapter()
    calls = []

    def fake_run(argv, input_text, timeout_seconds):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, stdout="Not logged in", stderr="")

    monkeypatch.setattr(adapter, "_run_sync", fake_run)
    first = await adapter.readiness_check()
    second = await adapter.readiness_check()

    assert first.ready is False
    assert second.ready is False
    assert first.error_code == "codex_direct_not_logged_in"
    assert second.error_code == "codex_direct_not_logged_in"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_codex_direct_readiness_cache_can_be_disabled(monkeypatch, fake_codex, codex_home):
    _enable_direct(monkeypatch, fake_codex=fake_codex, home=codex_home)
    monkeypatch.setenv("CODEX_DIRECT_READINESS_CACHE_SECONDS", "0")
    adapter = CodexDirectAdapter()
    calls = []

    def fake_run(argv, input_text, timeout_seconds):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="Logged in using ChatGPT", stderr="")

    monkeypatch.setattr(adapter, "_run_sync", fake_run)
    first = await adapter.readiness_check()
    second = await adapter.readiness_check()

    assert first.ready is True
    assert second.ready is True
    assert first.safe_summary["readiness_cache_hit"] is False
    assert second.safe_summary["readiness_cache_hit"] is False
    assert second.safe_summary["readiness_cache_ttl_seconds"] == 0
    assert len(calls) == 2


def test_provider_registry_resolves_codex_direct(monkeypatch, fake_codex, codex_home):
    _enable_direct(monkeypatch, fake_codex=fake_codex, home=codex_home)
    import app.services.provider_runtime as provider_runtime_module
    provider_runtime_module._BOOTSTRAPPED = False
    bootstrap_provider_runtime()
    adapter = ProviderRegistry.get("codex_direct", Mock())
    assert isinstance(adapter, CodexDirectAdapter)


class SuccessAdapter(ProviderAdapter):
    name = "success_provider"

    async def generate(self, db, req):
        return ProviderResult(
            ok=True,
            provider=self.name,
            elapsed_ms=1,
            structured_output={
                "customer_reply": "ok",
                "language": "en",
                "intent": "greeting",
                "handoff_required": False,
                "ticket_should_create": False,
                "tool_calls": [],
                "evidence_used": [],
            },
            raw_payload_safe_summary={"unit": True},
        )


class FailureAdapter(ProviderAdapter):
    def __init__(self, name: str, error_code: str, fallback_allowed: bool = True):
        self.name = name
        self.error_code = error_code
        self.fallback_allowed = fallback_allowed

    async def generate(self, db, req):
        return ProviderResult.unavailable(self.name, self.error_code, 1, fallback_allowed=self.fallback_allowed)


def _mock_router_db():
    db = Mock()
    rule = Mock()
    rule.mappings.return_value.first.return_value = None

    def execute(stmt, params=None, *args, **kwargs):
        query = str(stmt).lower()
        if "insert into provider_runtime_audit_logs" in query:
            return Mock()
        return rule

    db.execute.side_effect = execute
    return db


@pytest.mark.asyncio
async def test_router_selects_codex_direct_from_env(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_PRIMARY_PROVIDER", "codex_direct")
    monkeypatch.setenv("PROVIDER_RUNTIME_FALLBACK_PROVIDERS", "")
    import app.services.provider_runtime as provider_runtime_module
    monkeypatch.setattr(provider_runtime_module, "bootstrap_provider_runtime", lambda: None)
    ProviderRegistry.register("codex_direct", lambda db: SuccessAdapter())
    res = await ProviderRuntimeRouter(_mock_router_db()).route(_request())
    assert res.ok
    assert res.provider == "success_provider"


@pytest.mark.asyncio
async def test_router_codex_direct_fallback_can_succeed(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_PRIMARY_PROVIDER", "codex_direct")
    monkeypatch.setenv("PROVIDER_RUNTIME_FALLBACK_PROVIDERS", "rule_engine")
    import app.services.provider_runtime as provider_runtime_module
    monkeypatch.setattr(provider_runtime_module, "bootstrap_provider_runtime", lambda: None)
    ProviderRegistry.register("codex_direct", lambda db: FailureAdapter("codex_direct", "codex_direct_timeout"))
    ProviderRegistry.register("rule_engine", lambda db: SuccessAdapter())
    res = await ProviderRuntimeRouter(_mock_router_db()).route(_request())
    assert res.ok
    assert res.provider == "success_provider"


@pytest.mark.asyncio
async def test_router_preserves_codex_direct_primary_error_after_failed_fallback(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_PRIMARY_PROVIDER", "codex_direct")
    monkeypatch.setenv("PROVIDER_RUNTIME_FALLBACK_PROVIDERS", "rule_engine")
    import app.services.provider_runtime as provider_runtime_module
    monkeypatch.setattr(provider_runtime_module, "bootstrap_provider_runtime", lambda: None)
    ProviderRegistry.register("codex_direct", lambda db: FailureAdapter("codex_direct", "codex_direct_auth_missing"))
    ProviderRegistry.register("rule_engine", lambda db: FailureAdapter("rule_engine", "rule_engine_skeleton_unavailable"))
    res = await ProviderRuntimeRouter(_mock_router_db()).route(_request())
    assert not res.ok
    assert res.provider == "codex_direct"
    assert res.error_code == "codex_direct_auth_missing"


@pytest.mark.asyncio
async def test_router_codex_direct_kill_switch_uses_fallback(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_PRIMARY_PROVIDER", "codex_direct")
    monkeypatch.setenv("PROVIDER_RUNTIME_FALLBACK_PROVIDERS", "rule_engine")
    monkeypatch.setenv("PROVIDER_RUNTIME_KILL_SWITCH", "true")
    import app.services.provider_runtime as provider_runtime_module
    monkeypatch.setattr(provider_runtime_module, "bootstrap_provider_runtime", lambda: None)
    ProviderRegistry.register("codex_direct", lambda db: FailureAdapter("codex_direct", "should_not_run", fallback_allowed=False))
    ProviderRegistry.register("rule_engine", lambda db: SuccessAdapter())
    res = await ProviderRuntimeRouter(_mock_router_db()).route(_request())
    assert res.ok
    assert res.provider == "success_provider"


def test_admin_routing_codex_direct_defaults_to_no_fallback(monkeypatch):
    from app.api.admin_provider_runtime import WebchatFastRoutingUpdate
    payload = WebchatFastRoutingUpdate(primary_provider="codex_direct")
    payload.validate_allowed()
    assert payload.fallback_providers == []
