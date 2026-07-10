from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.nexus_osr import runtime_evidence_transport as transport  # noqa: E402

PUBLIC_TEST_IP = "93.184.216.34"
CLI = REPO_ROOT / "backend" / "scripts" / "probe_nexus_osr_runtime_evidence.py"


def _run_probe(body: bytes) -> dict:
    return transport.run_read_only_http_probe(
        transport.ReadOnlyProbeSpec(
            path="runtime_decision",
            endpoint="/api/admin/osr/runtime-decision-audits?limit=1",
        ),
        base_url="https://staging.example.test",
        allowed_hosts=["staging.example.test"],
        tenant_id="tenant-a",
        bearer_token="not-emitted",
        resolver=lambda _host, _port: [PUBLIC_TEST_IP],
        executor=lambda *_args: (200, body),
    )


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"{",
        b"\xff",
        (b"[" * 2_000) + b"0" + (b"]" * 2_000),
    ],
    ids=["empty", "malformed", "invalid-utf8", "too-deep"],
)
def test_invalid_or_too_deep_probe_json_is_bounded_payload_failure(body: bytes) -> None:
    result = _run_probe(body)

    assert result == {
        "path": "runtime_decision",
        "method": "GET",
        "permission_granted": True,
        "status_code": 200,
        "payload": {},
        "observed_at": result["observed_at"],
        "error_code": "payload_invalid",
    }
    assert "not-emitted" not in json.dumps(result)


@pytest.mark.parametrize("body", [b"[]", b"null", b'"text"', b"1"])
def test_valid_non_object_probe_json_is_payload_invalid(body: bytes) -> None:
    result = _run_probe(body)

    assert result["error_code"] == "payload_invalid"
    assert result["status_code"] == 200
    assert result["permission_granted"] is True
    assert result["payload"] == {}


def test_valid_bounded_probe_object_is_preserved() -> None:
    payload = {
        "tenant_id": "tenant-a",
        "state": "ready",
        "observed_at": "2026-07-10T22:50:00+00:00",
        "evidence_count": 1,
    }
    result = _run_probe(json.dumps(payload).encode("utf-8"))

    assert "error_code" not in result
    assert result["status_code"] == 200
    assert result["permission_granted"] is True
    assert result["payload"] == payload


def test_unexpected_decoder_exception_is_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_decoder(_value: str):
        raise RuntimeError("decoder implementation failed")

    monkeypatch.setattr(transport.json, "loads", fail_decoder)
    result = _run_probe(b"{}")

    assert result["error_code"] == "payload_invalid"
    assert result["payload"] == {}
    assert result["status_code"] == 200


def test_cli_artifact_metrics_stdout_and_exit_share_invalid_probe_snapshot(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc).isoformat()
    identity = {
        "code_sha": "a" * 40,
        "config_sha256": "b" * 64,
        "build_id": "build-1",
        "migration_head": "20260710_0056",
        "observed_at": now,
    }
    budget = {
        "path": "runtime_decision",
        "owner": "nexus-osr-runtime",
        "rationale": "bounded",
        "window_seconds": 300,
        "min_sample_size": 1,
        "max_error_ratio": 0.02,
        "max_unavailable_ratio": 0.01,
        "max_fail_closed_ratio": 0.05,
        "max_p95_latency_ms": 1000,
        "max_backlog": 0,
    }
    sample = {
        "requests": 1,
        "errors": 0,
        "unavailable": 0,
        "fail_closed": 0,
        "redaction_failures": 0,
        "p95_latency_ms": 10,
        "backlog": 0,
    }
    probe = _run_probe(b"{")
    files = {
        "config.json": {
            "max_evidence_age_seconds": 900,
            "failure_budgets": [budget],
            "probes": [
                {
                    "path": "runtime_decision",
                    "endpoint": "/api/admin/osr/runtime-decision-audits?limit=1",
                    "method": "GET",
                }
            ],
        },
        "expected.json": identity,
        "observed.json": identity,
        "samples.json": {"runtime_decision": sample},
        "probes.json": [probe],
    }
    for name, payload in files.items():
        (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")

    artifact = tmp_path / "artifact.json"
    metrics = tmp_path / "metrics.prom"
    completed = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--config",
            str(tmp_path / "config.json"),
            "--expected-identity",
            str(tmp_path / "expected.json"),
            "--observed-identity",
            str(tmp_path / "observed.json"),
            "--samples",
            str(tmp_path / "samples.json"),
            "--probe-fixtures",
            str(tmp_path / "probes.json"),
            "--tenant",
            "tenant-a",
            "--artifact",
            str(artifact),
            "--metrics",
            str(metrics),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2, completed.stderr + completed.stdout
    snapshot = json.loads(artifact.read_text(encoding="utf-8"))
    summary = json.loads(completed.stdout)
    metric_text = metrics.read_text(encoding="utf-8")
    assert snapshot["state"] == "not_ready"
    assert "payload_invalid" in snapshot["reason_codes"]
    assert summary["state"] == snapshot["state"]
    assert summary["reason_codes"] == snapshot["reason_codes"]
    assert summary["artifact_bytes"] == len(json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    assert 'nexus_osr_runtime_evidence_state{state="not_ready"} 1' in metric_text
    assert 'nexus_osr_runtime_evidence_state{state="ready"} 1' not in metric_text
