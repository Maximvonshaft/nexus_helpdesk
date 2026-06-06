from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.services.provider_runtime.adapters.codex_direct import CodexDirectAdapter
from app.services.provider_runtime.schemas import ProviderRequest


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
    print("Logged in using ChatGPT")
    raise SystemExit(0)

sleep_seconds = float(os.environ.get("CODEX_FAKE_SLEEP", "0") or "0")
if sleep_seconds:
    time.sleep(sleep_seconds)

print(json.dumps({
    "customer_reply": "I can help with that. Please share your tracking number.",
    "language": "en",
    "intent": "tracking_lookup",
    "handoff_required": False,
    "ticket_should_create": False,
    "tool_calls": [],
    "evidence_used": [],
    "confidence": 0.9,
    "reason": "User asked for tracking.",
    "risk_level": "low",
    "next_action": "reply",
    "safety_notes": []
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
    for key in list(__import__("os").environ):
        if key.startswith("CODEX_") or key.startswith("PROVIDER_RUNTIME_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("APP_ENV", "test")


def _enable_direct(monkeypatch, *, fake_codex: Path, home: Path, timeout: int = 5):
    monkeypatch.setenv("CODEX_DIRECT_ENABLED", "true")
    monkeypatch.setenv("CODEX_DIRECT_COMMAND", str(fake_codex))
    monkeypatch.setenv("CODEX_DIRECT_HOME", str(home))
    monkeypatch.setenv("CODEX_DIRECT_MODEL", "test-codex-model")
    monkeypatch.setenv("CODEX_DIRECT_TIMEOUT_SECONDS", str(timeout))
    monkeypatch.setenv("CODEX_DIRECT_EXEC_ARGS_TEMPLATE", "exec --model {model} -")


def _request(**overrides) -> ProviderRequest:
    data = {
        "request_id": "req-latency-1",
        "tenant_id": "default",
        "tenant_key": "default",
        "channel_key": "website",
        "session_id": "sess-latency-1",
        "scenario": "webchat_fast_reply",
        "body": "Please help me track my parcel.",
        "recent_context": [],
        "tracking_fact_summary": None,
        "tracking_fact_evidence_present": False,
        "output_contract": "speedaf_webchat_fast_reply_v1",
        "timeout_ms": 5000,
        "metadata": {"knowledge_context": {}, "persona_context": {}},
    }
    data.update(overrides)
    return ProviderRequest(**data)


@pytest.mark.asyncio
async def test_codex_direct_success_includes_latency_breakdown(monkeypatch, fake_codex, codex_home):
    _enable_direct(monkeypatch, fake_codex=fake_codex, home=codex_home)

    result = await CodexDirectAdapter().generate(Mock(), _request())

    assert result.ok is True
    latency = result.raw_payload_safe_summary["latency"]
    assert latency["total_ms"] >= 0
    assert latency["readiness_ms"] >= 0
    assert latency["prompt_build_ms"] >= 0
    assert latency["argv_build_ms"] >= 0
    assert latency["subprocess_ms"] >= 0
    assert latency["parse_ms"] >= 0
    assert result.raw_payload_safe_summary["timeout_seconds"] == 5.0


@pytest.mark.asyncio
async def test_codex_direct_timeout_includes_timeout_source_and_latency(monkeypatch, fake_codex, codex_home):
    _enable_direct(monkeypatch, fake_codex=fake_codex, home=codex_home, timeout=1)
    monkeypatch.setenv("CODEX_FAKE_SLEEP", "3")

    result = await CodexDirectAdapter().generate(Mock(), _request(timeout_ms=1000))

    assert result.ok is False
    assert result.error_code == "codex_direct_timeout"
    assert result.raw_payload_safe_summary["timeout_source"] == "codex_direct_subprocess"
    assert result.raw_payload_safe_summary["timeout_seconds"] == 1.0
    latency = result.raw_payload_safe_summary["latency"]
    assert latency["total_ms"] >= 0
    assert latency["readiness_ms"] >= 0
    assert latency["prompt_build_ms"] >= 0
    assert latency["argv_build_ms"] >= 0
    assert latency["subprocess_ms"] >= 0
    assert "parse_ms" not in latency
