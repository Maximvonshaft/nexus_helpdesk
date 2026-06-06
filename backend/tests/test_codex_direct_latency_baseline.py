from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "bench_codex_direct_webchat_fast.py"


def load_module():
    spec = importlib.util.spec_from_file_location("bench_codex_direct_webchat_fast", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_percentile_uses_interpolated_values():
    bench = load_module()

    assert bench.percentile([100, 200, 300, 400], 0.50) == 250
    assert bench.percentile([100, 200, 300, 400], 0.95) == 385


def test_normalize_audit_row_extracts_codex_latency_fields():
    bench = load_module()
    row = {
        "created_at": "2026-06-07T10:00:00+00:00",
        "request_id": "req-1",
        "session_id": "sess-1",
        "provider": "codex_direct",
        "operation": "generate",
        "status": "ok",
        "error_code": None,
        "elapsed_ms": 4810,
        "safe_summary": json.dumps(
            {
                "provider": "codex_direct",
                "prompt_chars": 3012,
                "stdout_chars": 800,
                "stderr_chars": 0,
                "latency": {
                    "readiness_ms": 61,
                    "prompt_build_ms": 2,
                    "argv_build_ms": 0,
                    "subprocess_ms": 4741,
                    "parse_ms": 0,
                    "total_ms": 4808,
                },
            }
        ),
    }

    record = bench.normalize_audit_row(row, benchmark_profile="observed_audit")

    assert record["ok"] is True
    assert record["request_id"] == "req-1"
    assert record["benchmark_profile"] == "observed_audit"
    assert record["prompt_chars"] == 3012
    assert record["codex_readiness_ms"] == 61
    assert record["codex_subprocess_ms"] == 4741
    assert record["codex_total_ms"] == 4808
    assert record["timeout_hit"] is False


def test_normalize_timeout_marks_failover_worthy():
    bench = load_module()
    record = bench.normalize_record(
        source="unit",
        benchmark_profile="direct",
        sample_index=0,
        request_id="req-timeout",
        session_id="sess",
        provider="codex_direct",
        status="failed",
        error_code="codex_direct_timeout",
        elapsed_ms=26000,
        safe_summary={
            "timeout_source": "codex_direct_subprocess",
            "prompt_chars": 1500,
            "latency": {"readiness_ms": 10, "subprocess_ms": 25000, "total_ms": 26000},
        },
    )

    assert record["ok"] is False
    assert record["timeout_hit"] is True
    assert record["failover_worthy"] is True
    assert record["codex_total_ms"] == 26000


def test_summary_recommends_prompt_compression_when_p95_under_budget():
    bench = load_module()
    records = [
        bench.normalize_record(
            source="unit",
            benchmark_profile="direct",
            sample_index=index,
            request_id=f"req-{index}",
            session_id="sess",
            provider="codex_direct",
            status="ok",
            error_code=None,
            elapsed_ms=4000 + index,
            safe_summary={"latency": {"total_ms": 4000 + index, "subprocess_ms": 3900 + index}},
        )
        for index in range(20)
    ]

    summary = bench.summarize_records(records)

    assert summary["sample_count"] == 20
    assert summary["failure_rate"] == 0
    assert summary["timeout_rate"] == 0
    assert summary["recommendation"] == "proceed_to_prompt_compression"
    assert summary["metrics"]["codex_total_ms"]["p95"] < 8000


def test_markdown_report_includes_scope_guard():
    bench = load_module()
    records = [
        {
            "timestamp": "2026-06-07T10:00:00+00:00",
            "benchmark_profile": "direct",
            "status": "ok",
            "error_code": None,
            "codex_total_ms": 4800,
            "codex_subprocess_ms": 4700,
            "prompt_chars": 1800,
            "stdout_chars": 700,
            "stderr_chars": 0,
        }
    ]
    summary = bench.summarize_records(records)

    report = bench.render_markdown_report(summary, records, title="Codex Direct Latency Baseline")

    assert "Scope guard" in report
    assert "does not create tickets" in report
    assert "does not change Speedaf API behavior" in report
