from __future__ import annotations

import importlib.util
import json
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
    body = {
        "ok": True,
        "reply_source": "codex_direct",
        "reply": "Please verify the waybill number you provided.",
        "ai_decision_trace": {
            "policy_gate": {"ok": True},
            "phase_timings": {
                "tracking_fact_elapsed_ms": 8,
                "runtime_context_elapsed_ms": 2,
                "provider_elapsed_ms": 6100,
                "policy_gate_elapsed_ms": 1,
                "total_elapsed_ms": 6200,
            },
        },
    }

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return json.dumps(self.body).encode("utf-8")


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
        require_provider_audit=False,
    )


def test_smoke_request_omits_origin_by_default(monkeypatch):
    bench = load_module()
    captured = {}

    def fake_urlopen(request, timeout):
        captured["headers"] = dict(request.header_items())
        return _FakeResponse()

    monkeypatch.setattr(bench.urllib.request, "urlopen", fake_urlopen)
    record = bench.smoke_once(_args(origin=None), 0)

    assert "Origin" not in captured["headers"]
    assert captured["headers"]["Content-type"] == "application/json"
    assert record["session_id"]
    assert record["client_message_id"]
    assert record["reply"] == "Please verify the waybill number you provided."


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
    assert summary["provider_audit_missing_count"] == 2
    assert summary["raw_identifier_leak_count"] == 0
    assert summary["live_status_claim_count"] == 0
    assert summary["tracking_fact_ms"]["p50"] == 8.0
    assert summary["provider_ms"]["p50"] == 700.0


def test_reply_leak_and_payload_leak_are_separate(monkeypatch):
    bench = load_module()

    class PayloadLeakResponse(_FakeResponse):
        body = {
            **_FakeResponse.body,
            "reply": "Please verify the waybill number you provided.",
            "debug_trace": {"normalized_query": "customer asked about CH1200000011425"},
        }

    monkeypatch.setattr(bench.urllib.request, "urlopen", lambda request, timeout: PayloadLeakResponse())
    record = bench.smoke_once(_args(), 0)

    assert record["reply_raw_identifier_leaked"] is False
    assert record["payload_raw_identifier_leaked"] is True
    assert record["raw_identifier_leaked"] is False
    assert record["raw_identifier_leak_paths"] == ["$.debug_trace.normalized_query"]
    assert record["effective_success"] is True


def test_ch_format_guidance_is_not_raw_identifier_leak(monkeypatch):
    bench = load_module()

    class FormatGuidanceResponse(_FakeResponse):
        body = {
            **_FakeResponse.body,
            "reply": "Please check that the number starts with CH and uses CH + 12 digits.",
        }

    monkeypatch.setattr(bench.urllib.request, "urlopen", lambda request, timeout: FormatGuidanceResponse())
    record = bench.smoke_once(_args(), 0)

    assert record["reply_raw_identifier_leaked"] is False
    assert record["payload_raw_identifier_leaked"] is False
    assert record["effective_success"] is True


def test_exact_submitted_waybill_is_raw_identifier_leak(monkeypatch):
    bench = load_module()

    class ExactLeakResponse(_FakeResponse):
        body = {
            **_FakeResponse.body,
            "reply": "I could not find CH1200000011425. Please verify 1200000011425.",
        }

    monkeypatch.setattr(bench.urllib.request, "urlopen", lambda request, timeout: ExactLeakResponse())
    record = bench.smoke_once(_args(), 0)

    assert record["reply_raw_identifier_leaked"] is True
    assert record["payload_raw_identifier_leaked"] is True
    assert record["raw_identifier_leak_paths"] == ["$.reply"]
    assert record["effective_success"] is False


def test_require_provider_audit_fails_without_database_url():
    bench = load_module()
    args = _args()
    args.require_provider_audit = True

    try:
        bench.smoke_once(args, 0)
    except SystemExit as exc:
        assert "--require-provider-audit requires" in str(exc)
    else:
        raise AssertionError("expected SystemExit")


def test_require_provider_audit_fails_when_no_audit_row(monkeypatch):
    bench = load_module()
    args = _args()
    args.database_url = "sqlite:///:memory:"
    args.require_provider_audit = True
    monkeypatch.setattr(bench.urllib.request, "urlopen", lambda request, timeout: _FakeResponse())
    monkeypatch.setattr(bench, "query_provider_audit_rows", lambda **_kwargs: [])

    try:
        bench.smoke_once(args, 0)
    except SystemExit as exc:
        assert "found no provider_runtime_audit_logs rows" in str(exc)
    else:
        raise AssertionError("expected SystemExit")


def test_prompt_codex_and_subprocess_metrics_populate_from_audit(monkeypatch):
    bench = load_module()
    args = _args()
    args.database_url = "sqlite:///:memory:"
    audit_row = {
        "provider": "codex_direct",
        "status": "ok",
        "error_code": None,
        "elapsed_ms": 6206,
        "safe_summary": json.dumps(
            {
                "prompt_chars": 4554,
                "readiness_cache_hit": True,
                "readiness_ms": 1,
                "latency": {"total_ms": 6206, "subprocess_ms": 6105, "readiness_ms": 1},
            }
        ),
    }
    monkeypatch.setattr(bench.urllib.request, "urlopen", lambda request, timeout: _FakeResponse())
    monkeypatch.setattr(bench, "query_provider_audit_rows", lambda **_kwargs: [audit_row])

    record = bench.smoke_once(args, 0)
    summary = bench.summarize([record])

    assert record["provider_audit_available"] is True
    assert record["prompt_chars"] == 4554
    assert record["codex_total_ms"] == 6206
    assert record["subprocess_ms"] == 6105
    assert record["readiness_cache_hit"] is True
    assert record["readiness_ms"] == 1
    assert summary["provider_audit_missing_count"] == 0
    assert summary["prompt_chars"]["p50"] == 4554.0
    assert summary["codex_total_ms"]["p50"] == 6206.0
    assert summary["subprocess_ms"]["p50"] == 6105.0
