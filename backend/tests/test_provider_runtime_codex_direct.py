from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.services.provider_runtime import bootstrap_provider_runtime
from app.services.provider_runtime.adapters.codex_direct import CodexDirectAdapter
from app.services.provider_runtime.registry import ProviderRegistry
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
    print("fake nonzero", file=sys.stderr)
    raise SystemExit(exit_code)

mode = os.environ.get("CODEX_FAKE_MODE", "success")
if mode == "empty":
    raise SystemExit(0)
if mode == "bad_json":
    print("this is not json")
    raise SystemExit(0)
if mode == "fenced":
    print("```json")
    print(json.dumps({"customer_reply":"fenced ok","language":"en","intent":"other","handoff_required":False,"ticket_should_create":False}))
    print("```")
    raise SystemExit(0)

_ = sys.stdin.read()
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
    "reason": "User wants parcel tracking."
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
def clean_codex_env(monkeypatch):
    for key in list(
        {
            "CODEX_DIRECT_ENABLED",
            "CODEX_DIRECT_COMMAND",
            "CODEX_DIRECT_HOME",
            "CODEX_DIRECT_MODEL",
            "CODEX_DIRECT_TIMEOUT_SECONDS",
            "CODEX_DIRECT_MAX_PROMPT_CHARS",
            "CODEX_DIRECT_REQUIRE_JSON",
            "CODEX_DIRECT_EXEC_ARGS_TEMPLATE",
            "CODEX_FAKE_LOGIN_STATUS",
            "CODEX_FAKE_LOGIN_EXIT",
            "CODEX_FAKE_SLEEP",
            "CODEX_FAKE_EXIT",
            "CODEX_FAKE_MODE",
            "PROVIDER_RUNTIME_PRIMARY_PROVIDER",
            "PROVIDER_RUNTIME_FALLBACK_PROVIDERS",
            "PROVIDER_RUNTIME_TIMEOUT_MS",
            "PROVIDER_RUNTIME_OUTPUT_CONTRACT",
        }
    ):
        monkeypatch.delenv(key, raising=False)


def _enable_direct(monkeypatch, *, fake_codex: Path, home: Path, timeout: int = 5):
    monkeypatch.setenv("CODEX_DIRECT_ENABLED", "true")
    monkeypatch.setenv("CODEX_DIRECT_COMMAND", str(fake_codex))
    monkeypatch.setenv("CODEX_DIRECT_HOME", str(home))
    monkeypatch.setenv("CODEX_DIRECT_MODEL", "test-codex-model")
    monkeypatch.setenv("CODEX_DIRECT_TIMEOUT_SECONDS", str(timeout))
    monkeypatch.setenv("CODEX_DIRECT_EXEC_ARGS_TEMPLATE", "exec --model {model} -")


def _request() -> ProviderRequest:
    return ProviderRequest(
        request_id="req-1",
        tenant_id="default",
        tenant_key="default",
        channel_key="website",
        session_id="sess-1",
        scenario="webchat_fast_reply",
        body="Please help me track my parcel.",
        recent_context=[],
        tracking_fact_summary=None,
        tracking_fact_evidence_present=False,
        output_contract="speedaf_webchat_fast_reply_v1",
        timeout_ms=5000,
        metadata={
            "context_version": "nexus_webchat_runtime_context_v2",
            "knowledge_context": {"hits": [], "locked_facts": [], "evidence_pack": []},
        },
    )


@pytest.mark.asyncio
async def test_codex_direct_disabled():
    res = await CodexDirectAdapter().generate(Mock(), _request())
    assert not res.ok
    assert res.error_code == "codex_direct_disabled"
    assert res.fallback_allowed is False


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
async def test_codex_direct_nonzero_exit(monkeypatch, fake_codex, codex_home):
    _enable_direct(monkeypatch, fake_codex=fake_codex, home=codex_home)
    monkeypatch.setenv("CODEX_FAKE_EXIT", "2")
    res = await CodexDirectAdapter().generate(Mock(), _request())
    assert not res.ok
    assert res.error_code == "codex_direct_nonzero_exit"
    assert "test" not in json.dumps(res.raw_payload_safe_summary).lower()


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
async def test_codex_direct_success_normalizes_json_and_tools(monkeypatch, fake_codex, codex_home):
    _enable_direct(monkeypatch, fake_codex=fake_codex, home=codex_home)
    res = await CodexDirectAdapter().generate(Mock(), _request())
    assert res.ok
    assert res.provider == "codex_direct"
    assert res.model == "test-codex-model"
    assert res.structured_output["customer_reply"].startswith("Sure.")
    assert res.structured_output["intent"] == "tracking"
    assert res.structured_output["tool_calls"] == [
        {"tool_name": "speedaf.order.query", "arguments": {"tracking_number": "SF123456789"}, "idempotency_key": None, "reason": None, "requires_confirmation": False}
    ]
    assert res.structured_output["evidence_used"][0]["source"] == "knowledge_base"


def test_codex_direct_smoke_ready(monkeypatch, fake_codex, codex_home):
    _enable_direct(monkeypatch, fake_codex=fake_codex, home=codex_home)
    smoke = CodexDirectAdapter().smoke_check()
    assert smoke["ready"] is True
    assert smoke["error_code"] is None
    assert "auth.json" in smoke["checks"]["auth_path"]


def test_provider_registry_resolves_codex_direct(monkeypatch, fake_codex, codex_home):
    _enable_direct(monkeypatch, fake_codex=fake_codex, home=codex_home)
    bootstrap_provider_runtime()
    adapter = ProviderRegistry.get("codex_direct", Mock())
    assert isinstance(adapter, CodexDirectAdapter)


@pytest.mark.asyncio
async def test_router_selects_codex_direct_from_env(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_PRIMARY_PROVIDER", "codex_direct")
    monkeypatch.setenv("PROVIDER_RUNTIME_FALLBACK_PROVIDERS", "")
    mock_db = Mock()
    mock_rule = Mock()
    mock_rule.mappings.return_value.first.return_value = None

    def mock_db_execute(stmt, params=None, *args, **kwargs):
        query = str(stmt).lower()
        if "insert into provider_runtime_audit_logs" in query:
            return Mock()
        return mock_rule

    mock_db.execute.side_effect = mock_db_execute
    ProviderRegistry.register(
        "codex_direct",
        lambda db: _SuccessAdapter(),
    )

    res = await ProviderRuntimeRouter(mock_db).route(_request())
    assert res.ok
    assert res.provider == "codex_direct"
    assert res.structured_output["customer_reply"] == "ok"


class _SuccessAdapter:
    name = "codex_direct"

    async def generate(self, db, req):
        return ProviderResult(
            ok=True,
            provider="codex_direct",
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
