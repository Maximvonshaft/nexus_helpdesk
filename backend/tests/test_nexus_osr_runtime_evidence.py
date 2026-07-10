from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.nexus_osr.runtime_evidence import (  # noqa: E402
    ALLOWED_STATES,
    MAX_ARTIFACT_BYTES,
    ReadOnlyProbeSpec,
    bounded_json_bytes,
    build_runtime_evidence_snapshot,
    compare_runtime_identity,
    evaluate_failure_budget,
    evaluate_probe_result,
    render_prometheus_metrics,
    run_read_only_http_probe,
    scan_for_unsafe_evidence,
    validate_read_only_probe_url,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "nexus_osr_runtime_evidence"
CONFIG = REPO_ROOT / "config" / "observability" / "nexus_osr_runtime_evidence.json"
ALERTS = REPO_ROOT / "deploy" / "observability" / "nexus-osr-runtime-alerts.yml"
NOW = datetime(2026, 7, 10, 20, 0, tzinfo=timezone.utc)


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _identity() -> tuple[dict, dict]:
    return _json(FIXTURES / "expected_identity.json"), _json(FIXTURES / "observed_identity.json")


def test_runtime_identity_ready_and_drift_fail_closed() -> None:
    expected, observed = _identity()
    ready = compare_runtime_identity(expected, observed, now=NOW, max_age_seconds=900)
    assert ready["state"] == "ready"
    assert ready["reason_codes"] == ["probe_ok"]

    drifted = dict(observed, config_sha256="b" * 64)
    result = compare_runtime_identity(expected, drifted, now=NOW, max_age_seconds=900)
    assert result["state"] == "not_ready"
    assert "config_drift" in result["reason_codes"]


def test_runtime_identity_stale_and_unavailable_fail_closed() -> None:
    expected, observed = _identity()
    stale = dict(observed, observed_at="2026-07-10T19:00:00Z")
    stale_result = compare_runtime_identity(expected, stale, now=NOW, max_age_seconds=900)
    assert stale_result["state"] == "not_ready"
    assert "evidence_stale" in stale_result["reason_codes"]

    missing = compare_runtime_identity(expected, None, now=NOW)
    assert missing["state"] == "unavailable"
    assert missing["reason_codes"] == ["identity_observed_missing"]


def test_failure_budget_and_redaction_failure_are_bounded() -> None:
    definition = {
        "path": "queue_worker",
        "owner": "nexus-osr-runtime",
        "rationale": "bounded",
        "window_seconds": 300,
        "min_sample_size": 10,
        "max_error_ratio": 0.02,
        "max_unavailable_ratio": 0.01,
        "max_fail_closed_ratio": 0.05,
        "max_p95_latency_ms": 1000,
        "max_backlog": 500,
    }
    healthy = evaluate_failure_budget(
        definition,
        {
            "requests": 100,
            "errors": 1,
            "unavailable": 0,
            "fail_closed": 2,
            "redaction_failures": 0,
            "p95_latency_ms": 300,
            "backlog": 10,
        },
    )
    assert healthy["state"] == "ready"
    assert healthy["ratios"]["error"] == 0.01

    failed = evaluate_failure_budget(
        definition,
        {
            "requests": 100,
            "errors": 1,
            "unavailable": 0,
            "fail_closed": 2,
            "redaction_failures": 1,
            "p95_latency_ms": 300,
            "backlog": 700,
        },
    )
    assert failed["state"] == "not_ready"
    assert {"redaction_failed", "queue_backlog_high", "budget_exhausted"} <= set(failed["reason_codes"])


def test_redaction_scanner_rejects_raw_sensitive_keys_and_values() -> None:
    assert scan_for_unsafe_evidence({"state": "ready", "sha256_prefix": "a" * 16})["safe"] is True
    assert scan_for_unsafe_evidence({"provider_payload": {"value": "hidden"}})["safe"] is False
    assert scan_for_unsafe_evidence({"summary": "Bearer abcdefghijklmnopqrstuvwxyz"})["safe"] is False
    assert scan_for_unsafe_evidence({"summary": "customer@example.com"})["safe"] is False


def test_probe_enforces_permission_tenant_freshness_and_redaction() -> None:
    base = {
        "path": "runtime_decision",
        "method": "GET",
        "permission_granted": True,
        "status_code": 200,
        "observed_at": "2026-07-10T19:59:30Z",
        "payload": {
            "tenant_id": "tenant-a",
            "state": "ready",
            "observed_at": "2026-07-10T19:59:30Z",
            "evidence_count": 1,
        },
    }
    assert evaluate_probe_result(base, expected_tenant_id="tenant-a", now=NOW)["state"] == "ready"

    denied = dict(base, permission_granted=False)
    denied_result = evaluate_probe_result(denied, expected_tenant_id="tenant-a", now=NOW)
    assert denied_result["state"] == "unavailable"
    assert "permission_denied" in denied_result["reason_codes"]

    wrong_tenant = dict(base, payload=dict(base["payload"], tenant_id="tenant-b"))
    tenant_result = evaluate_probe_result(wrong_tenant, expected_tenant_id="tenant-a", now=NOW)
    assert tenant_result["state"] == "not_ready"
    assert "tenant_scope_mismatch" in tenant_result["reason_codes"]
    assert "tenant-a" not in json.dumps(tenant_result)

    leaked = dict(base, payload=dict(base["payload"], email="customer@example.com"))
    leak_result = evaluate_probe_result(leaked, expected_tenant_id="tenant-a", now=NOW)
    assert leak_result["state"] == "not_ready"
    assert "redaction_failed" in leak_result["reason_codes"]


def test_snapshot_and_metrics_are_low_cardinality_and_tenant_safe() -> None:
    expected, observed = _identity()
    config = _json(CONFIG)
    snapshot = build_runtime_evidence_snapshot(
        tenant_id="tenant-a",
        expected_identity=expected,
        observed_identity=observed,
        budget_definitions=config["failure_budgets"],
        samples=_json(FIXTURES / "samples.json"),
        probes=_json(FIXTURES / "probes.json"),
        now=NOW,
        max_age_seconds=config["max_evidence_age_seconds"],
    )
    assert snapshot["state"] == "ready"
    assert snapshot["boundaries"]["read_only"] is True
    assert snapshot["boundaries"]["customer_message_sent"] is False
    assert snapshot["boundaries"]["tool_execution_performed"] is False

    metrics = render_prometheus_metrics(snapshot)
    assert "tenant-a" not in metrics
    assert "tenant_id=" not in metrics
    assert "conversation_id=" not in metrics
    assert "ticket_id=" not in metrics
    assert "tracking_number=" not in metrics
    assert 'path="runtime_decision"' in metrics
    assert 'state="ready"' in metrics
    assert len(metrics.splitlines()) < 200


def test_bounded_artifact_never_exceeds_limit() -> None:
    huge = {"schema": "x", "items": ["x" * 2000] * 100}
    encoded = bounded_json_bytes(huge)
    assert len(encoded) <= MAX_ARTIFACT_BYTES
    payload = json.loads(encoded)
    assert payload["state"] == "unavailable"
    assert payload["reason_codes"] == ["artifact_too_large"]


def test_read_only_probe_url_and_http_contract() -> None:
    assert (
        validate_read_only_probe_url(
            "https://staging.example.test",
            "/api/admin/osr/runtime-decision-audits?limit=1",
            allowed_hosts=["staging.example.test"],
        )
        == "https://staging.example.test/api/admin/osr/runtime-decision-audits?limit=1"
    )
    with pytest.raises(ValueError, match="unsafe_probe_url"):
        validate_read_only_probe_url(
            "https://staging.example.test",
            "/api/send/customer",
            allowed_hosts=["staging.example.test"],
        )

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit: int) -> bytes:
            return json.dumps(
                {
                    "tenant_id": "tenant-a",
                    "state": "ready",
                    "observed_at": "2026-07-10T19:59:30Z",
                    "evidence_count": 1,
                }
            ).encode()

    captured: dict[str, object] = {}

    def opener(request: Request, timeout: float):
        captured["method"] = request.get_method()
        captured["data"] = request.data
        captured["tenant"] = request.headers.get("X-nexus-tenant")
        captured["timeout"] = timeout
        return FakeResponse()

    result = run_read_only_http_probe(
        ReadOnlyProbeSpec(
            path="runtime_decision",
            endpoint="/api/admin/osr/runtime-decision-audits?limit=1",
        ),
        base_url="https://staging.example.test",
        allowed_hosts=["staging.example.test"],
        tenant_id="tenant-a",
        bearer_token="not-emitted",
        opener=opener,
    )
    assert result["status_code"] == 200
    assert captured["method"] == "GET"
    assert captured["data"] is None
    assert captured["tenant"] == "tenant-a"
    assert "not-emitted" not in json.dumps(result)


def test_alert_rules_are_valid_bounded_json_yaml() -> None:
    rules = _json(ALERTS)
    assert set(rules) == {"groups"}
    alerts = [rule for group in rules["groups"] for rule in group["rules"]]
    names = {item["alert"] for item in alerts}
    assert {
        "NexusOSRRuntimeIdentityDrift",
        "NexusOSREvidenceUnavailable",
        "NexusOSRFailureBudgetExhausted",
        "NexusOSRQueueBacklogHigh",
        "NexusOSRProviderRuntimeNotReady",
        "NexusOSRRedactionFailure",
    } <= names
    for item in alerts:
        assert set(item["labels"]) <= {"severity", "owner", "reason_family"}
        serialized = json.dumps(item)
        for forbidden in ("tenant_id", "conversation_id", "ticket_id", "tracking_number", "provider_group_id"):
            assert forbidden not in serialized


def test_synthetic_cli_integration_writes_bounded_artifacts(tmp_path: Path) -> None:
    artifact = tmp_path / "runtime-evidence.json"
    metrics = tmp_path / "runtime-evidence.prom"
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "backend" / "scripts" / "probe_nexus_osr_runtime_evidence.py"),
            "--config",
            str(CONFIG),
            "--expected-identity",
            str(FIXTURES / "expected_identity.json"),
            "--observed-identity",
            str(FIXTURES / "observed_identity.json"),
            "--samples",
            str(FIXTURES / "samples.json"),
            "--probe-fixtures",
            str(FIXTURES / "probes.json"),
            "--tenant",
            "tenant-a",
            "--artifact",
            str(artifact),
            "--metrics",
            str(metrics),
            "--now",
            "2026-07-10T20:00:00Z",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    snapshot = _json(artifact)
    assert snapshot["state"] == "ready"
    assert artifact.stat().st_size <= MAX_ARTIFACT_BYTES + 1
    assert "tenant-a" not in metrics.read_text(encoding="utf-8")
    assert set(item["state"] for item in snapshot["probes"]) <= set(ALLOWED_STATES)
