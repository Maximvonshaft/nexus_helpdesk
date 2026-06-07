from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "bench_codex_direct_webchat_latency.py"


def load_module():
    spec = importlib.util.spec_from_file_location("bench_codex_direct_webchat_latency", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return b'{"ok":true,"reply_source":"codex_direct","reply":"Please verify the waybill number you provided.","ai_decision_trace":{"policy_gate":{"ok":true},"phase_timings":{"tracking_fact_elapsed_ms":8,"runtime_context_elapsed_ms":2,"provider_elapsed_ms":6100,"policy_gate_elapsed_ms":1,"total_elapsed_ms":6200}}}'


def _args(*, origin=None):
    return SimpleNamespace(
        endpoint_url="http://127.0.0.1:18081/api/webchat/fast-reply",
        tenant_key="default",
        channel_key="website",
        body="CH1200000011425",
        country_code="CH",
        label="unit",
        origin=origin,
        timeout_seconds=10,
        database_url=None,
    )


def test_smoke_request_omits_origin_by_default(monkeypatch):
    bench = load_module()
    captured = {}

    def fake_urlopen(request, timeout):
        captured["headers"] = dict(request.header_items())
        return _FakeResponse()

    monkeypatch.setattr(bench.urllib.request, "urlopen", fake_urlopen)
    bench.smoke_once(_args(origin=None), 0)

    assert "Origin" not in captured["headers"]
    assert captured["headers"]["Content-type"] == "application/json"


def test_smoke_request_includes_explicit_origin(monkeypatch):
    bench = load_module()
    captured = {}

    def fake_urlopen(request, timeout):
        captured["headers"] = dict(request.header_items())
        return _FakeResponse()

    monkeypatch.setattr(bench.urllib.request, "urlopen", fake_urlopen)
    bench.smoke_once(_args(origin="https://support.example"), 0)

    assert captured["headers"]["Origin"] == "https://support.example"


def test_summary_counts_effective_failures_for_fallback_and_temporarily_unavailable():
    bench = load_module()
    summary = bench.summarize(
        [
            {
                "http_status": 200,
                "effective_success": True,
                "server_safe_fallback": False,
                "temporarily_unavailable_leaked": False,
                "codex_timeout": False,
                "raw_identifier_leaked": False,
                "live_status_claim_leaked": False,
                "elapsed_ms": 1000,
                "tracking_fact_ms": 8,
                "provider_ms": 700,
                "codex_latency_total_ms": 650,
                "codex_latency_subprocess_ms": 640,
                "codex_prompt_chars": 4554,
            },
            {
                "http_status": 200,
                "effective_success": False,
                "server_safe_fallback": True,
                "temporarily_unavailable_leaked": True,
                "codex_timeout": True,
                "raw_identifier_leaked": False,
                "live_status_claim_leaked": False,
                "elapsed_ms": 25000,
            },
        ]
    )

    assert summary["success_count"] == 2
    assert summary["failure_count"] == 0
    assert summary["effective_success_count"] == 1
    assert summary["effective_failure_count"] == 1
    assert summary["effective_success_rate"] == 0.5
    assert summary["server_safe_fallback_count"] == 1
    assert summary["temporarily_unavailable_count"] == 1
    assert summary["codex_timeout_count"] == 1
    assert summary["raw_identifier_leak_count"] == 0
    assert summary["live_status_claim_count"] == 0
    assert summary["tracking_fact_ms"]["p50"] == 8.0
    assert summary["provider_ms"]["p50"] == 700.0
