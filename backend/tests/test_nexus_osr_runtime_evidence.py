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

from app.services.nexus_osr.runtime_evidence import (  # noqa: E402
    ALLOWED_STATES,
    MAX_ARTIFACT_BYTES,
    ReadOnlyProbeSpec,
    bounded_json_bytes,
    build_runtime_evidence_snapshot,
    compare_runtime_identity,
    evaluate_failure_budget,
    evaluate_probe_result,
    finalize_runtime_evidence,
    prepare_read_only_probe_target,
    render_prometheus_metrics,
    run_read_only_http_probe,
    scan_for_unsafe_evidence,
    validate_read_only_probe_url,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "nexus_osr_runtime_evidence"
CONFIG = REPO_ROOT / "config" / "observability" / "nexus_osr_runtime_evidence.json"
ALERTS = REPO_ROOT / "deploy" / "observability" / "nexus-osr-runtime-alerts.yml"
CLI = REPO_ROOT / "backend" / "scripts" / "probe_nexus_osr_runtime_evidence.py"
NOW = datetime(2026, 7, 10, 20, 0, tzinfo=timezone.utc)
PUBLIC_TEST_IP = "93.184.216.34"


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _identity() -> tuple[dict, dict]:
    return _json(FIXTURES / "expected_identity.json"), _json(FIXTURES / "observed_identity.json")


def _run_cli(tmp_path: Path, *, config: dict, samples: dict, probes: list[dict]) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    expected, observed = _identity()
    paths = {
        "config.json": config,
        "expected.json": expected,
        "observed.json": observed,
        "samples.json": samples,
        "probes.json": probes,
    }
    for filename, payload in paths.items():
        (tmp_path / filename).write_text(json.dumps(payload), encoding="utf-8")
    artifact = tmp_path / "runtime-evidence.json"
    metrics = tmp_path / "runtime-evidence.prom"
    result = subprocess.run(
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
            "--now",
            "2026-07-10T20:00:00Z",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, artifact, metrics


def test_runtime_identity_ready_and_drift_fail_closed() -> None:
    expected, observed = _identity()
    ready = compare_runtime_identity(expected, observed, now=NOW, max_age_seconds=900)
    assert ready["state"] == "ready"
    assert ready["reason_codes"] == ["probe_ok"]

    result = compare_runtime_identity(expected, dict(observed, config_sha256="b" * 64), now=NOW)
    assert result["state"] == "not_ready"
    assert "config_drift" in result["reason_codes"]


def test_runtime_identity_stale_unavailable_and_invalid_age_fail_closed() -> None:
    expected, observed = _identity()
    stale = compare_runtime_identity(
        expected,
        dict(observed, observed_at="2026-07-10T19:00:00Z"),
        now=NOW,
        max_age_seconds=900,
    )
    assert stale["state"] == "not_ready"
    assert "evidence_stale" in stale["reason_codes"]

    missing = compare_runtime_identity(expected, None, now=NOW)
    assert missing["state"] == "unavailable"
    assert missing["reason_codes"] == ["identity_observed_missing"]

    malformed = compare_runtime_identity(expected, observed, now=NOW, max_age_seconds="900")
    assert malformed["state"] == "unavailable"
    assert "payload_invalid" in malformed["reason_codes"]


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


@pytest.mark.parametrize(
    ("sample_patch", "definition_patch"),
    [
        ({"requests": "unknown"}, {}),
        ({"requests": True}, {}),
        ({"p95_latency_ms": float("inf")}, {}),
        ({}, {"max_error_ratio": "0.02"}),
        ({}, {"max_unavailable_ratio": float("nan")}),
        ({}, {"max_backlog": -1}),
    ],
)
def test_malformed_numeric_evidence_is_unavailable_not_exception(sample_patch: dict, definition_patch: dict) -> None:
    definition = {
        "path": "runtime_decision",
        "owner": "nexus-osr-runtime",
        "window_seconds": 300,
        "min_sample_size": 1,
        "max_error_ratio": 0.02,
        "max_unavailable_ratio": 0.01,
        "max_fail_closed_ratio": 0.05,
        "max_p95_latency_ms": 1000,
        "max_backlog": 0,
        **definition_patch,
    }
    sample = {
        "requests": 100,
        "errors": 0,
        "unavailable": 0,
        "fail_closed": 0,
        "redaction_failures": 0,
        "p95_latency_ms": 100,
        "backlog": 0,
        **sample_patch,
    }
    result = evaluate_failure_budget(definition, sample)
    assert result["state"] == "unavailable"
    assert result["reason_codes"] == ["payload_invalid"]


def test_redaction_scanner_rejects_raw_sensitive_keys_and_values() -> None:
    assert scan_for_unsafe_evidence({"state": "ready", "sha256_prefix": "a" * 16})["safe"] is True
    assert scan_for_unsafe_evidence({"provider_payload": {"value": "hidden"}})["safe"] is False
    assert scan_for_unsafe_evidence({"summary": "Bearer abcdefghijklmnopqrstuvwxyz"})["safe"] is False
    assert scan_for_unsafe_evidence({"summary": "customer@example.com"})["safe"] is False


def test_probe_enforces_permission_tenant_freshness_redaction_and_numeric_contract() -> None:
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

    denied = evaluate_probe_result(dict(base, permission_granted=False), expected_tenant_id="tenant-a", now=NOW)
    assert denied["state"] == "unavailable"
    assert "permission_denied" in denied["reason_codes"]

    wrong_tenant = dict(base, payload=dict(base["payload"], tenant_id="tenant-b"))
    tenant_result = evaluate_probe_result(wrong_tenant, expected_tenant_id="tenant-a", now=NOW)
    assert tenant_result["state"] == "not_ready"
    assert "tenant_scope_mismatch" in tenant_result["reason_codes"]
    assert "tenant-a" not in json.dumps(tenant_result)

    leaked = dict(base, payload=dict(base["payload"], email="customer@example.com"))
    assert "redaction_failed" in evaluate_probe_result(leaked, expected_tenant_id="tenant-a", now=NOW)["reason_codes"]

    malformed = dict(base, payload=dict(base["payload"], evidence_count="many"))
    malformed_result = evaluate_probe_result(malformed, expected_tenant_id="tenant-a", now=NOW)
    assert malformed_result["state"] == "not_ready"
    assert "payload_invalid" in malformed_result["reason_codes"]


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


def test_bounded_artifact_never_exceeds_limit_and_returns_final_snapshot() -> None:
    huge = {"schema": "x", "state": "ready", "items": ["x" * 2000] * 100}
    final_snapshot, encoded = finalize_runtime_evidence(huge)
    assert len(encoded) <= MAX_ARTIFACT_BYTES
    assert final_snapshot["state"] == "unavailable"
    assert final_snapshot["reason_codes"] == ["artifact_too_large"]
    assert json.loads(encoded) == final_snapshot
    second_payload = json.loads(bounded_json_bytes(huge))
    assert second_payload["state"] == "unavailable"
    assert second_payload["reason_codes"] == ["artifact_too_large"]
    metrics = render_prometheus_metrics(final_snapshot)
    assert 'nexus_osr_runtime_evidence_state{state="unavailable"} 1' in metrics
    assert 'nexus_osr_runtime_evidence_state{state="ready"} 1' not in metrics


def test_read_only_probe_url_requires_https_allowlist_and_non_mutating_path() -> None:
    assert (
        validate_read_only_probe_url(
            "https://staging.example.test",
            "/api/admin/osr/runtime-decision-audits?limit=1",
            allowed_hosts=["staging.example.test"],
        )
        == "https://staging.example.test/api/admin/osr/runtime-decision-audits?limit=1"
    )
    for base_url, endpoint, allowed_hosts in (
        ("http://staging.example.test", "/api/admin/osr/audits", ["staging.example.test"]),
        ("https://user:pass@staging.example.test", "/api/admin/osr/audits", ["staging.example.test"]),
        ("https://staging.example.test", "/api/send/customer", ["staging.example.test"]),
        ("https://staging.example.test", "https://other.example.test/a", ["staging.example.test"]),
        ("https://staging.example.test", "/api/admin/osr/audits", ["other.example.test"]),
        ("https://staging.example.test", "/api/%73end/customer", ["staging.example.test"]),
        ("https://staging.example.test", "/api/../send/customer", ["staging.example.test"]),
        ("https://staging.example.test", "/api\\send\\customer", ["staging.example.test"]),
        ("https://staging.example.test", "/api/admin/osr/audits", ["staging.example.test?ignored=1"]),
    ):
        with pytest.raises(ValueError, match="unsafe_probe_url"):
            validate_read_only_probe_url(base_url, endpoint, allowed_hosts=allowed_hosts)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "169.254.1.1",
        "100.64.0.1",
        "224.0.0.1",
        "0.0.0.0",
        "192.0.2.1",
        "::1",
        "fe80::1",
        "fc00::1",
        "ff02::1",
        "::",
        "2001:db8::1",
    ],
)
def test_probe_rejects_non_public_ipv4_and_ipv6(address: str) -> None:
    with pytest.raises(ValueError, match="unsafe_probe_url"):
        prepare_read_only_probe_target(
            "https://staging.example.test",
            "/api/admin/osr/audits",
            allowed_hosts=["staging.example.test"],
            resolver=lambda _host, _port: [address],
        )


def test_http_probe_pins_validated_dns_result_and_never_re_resolves() -> None:
    resolver_calls: list[tuple[str, int]] = []
    captured: dict[str, object] = {}

    def resolver(host: str, port: int) -> list[str]:
        resolver_calls.append((host, port))
        return [PUBLIC_TEST_IP] if len(resolver_calls) == 1 else ["127.0.0.1"]

    def executor(target, address, headers, timeout, max_bytes):
        captured.update(target=target, address=address, headers=dict(headers), timeout=timeout, max_bytes=max_bytes)
        return 200, json.dumps(
            {
                "tenant_id": "tenant-a",
                "state": "ready",
                "observed_at": "2026-07-10T19:59:30Z",
                "evidence_count": 1,
            }
        ).encode()

    result = run_read_only_http_probe(
        ReadOnlyProbeSpec(path="runtime_decision", endpoint="/api/admin/osr/audits?limit=1"),
        base_url="https://staging.example.test",
        allowed_hosts=["staging.example.test"],
        tenant_id="tenant-a",
        bearer_token="not-emitted",
        resolver=resolver,
        executor=executor,
    )
    assert result["status_code"] == 200
    assert resolver_calls == [("staging.example.test", 443)]
    assert captured["address"] == PUBLIC_TEST_IP
    assert captured["target"].host == "staging.example.test"
    assert captured["headers"]["Host"] == "staging.example.test"
    assert "not-emitted" not in json.dumps(result)


def test_http_probe_blocks_redirect_without_forwarding_headers_to_a_second_target() -> None:
    calls: list[dict[str, object]] = []

    def executor(target, address, headers, timeout, max_bytes):
        calls.append({"target": target, "address": address, "headers": dict(headers)})
        return 302, b""

    result = run_read_only_http_probe(
        ReadOnlyProbeSpec(path="runtime_decision", endpoint="/api/admin/osr/audits"),
        base_url="https://staging.example.test",
        allowed_hosts=["staging.example.test"],
        tenant_id="tenant-a",
        bearer_token="staging-only-secret",
        resolver=lambda _host, _port: [PUBLIC_TEST_IP],
        executor=executor,
    )
    assert result["error_code"] == "unsafe_probe_url"
    assert result["status_code"] == 302
    assert len(calls) == 1
    assert calls[0]["target"].host == "staging.example.test"
    assert calls[0]["address"] == PUBLIC_TEST_IP
    assert "staging-only-secret" not in json.dumps(result)


def test_snapshot_rejects_non_sequence_collections_without_exception() -> None:
    expected, observed = _identity()
    snapshot = build_runtime_evidence_snapshot(
        tenant_id="tenant-a",
        expected_identity=expected,
        observed_identity=observed,
        budget_definitions=123,  # type: ignore[arg-type]
        samples={},
        probes=[],
        now=NOW,
    )
    assert snapshot["state"] == "unavailable"
    assert snapshot["reason_codes"] == ["payload_invalid"]


def test_http_probe_rejects_boolean_timeout_and_malformed_executor_response() -> None:
    common = dict(
        spec=ReadOnlyProbeSpec(path="runtime_decision", endpoint="/api/admin/osr/audits"),
        base_url="https://staging.example.test",
        allowed_hosts=["staging.example.test"],
        tenant_id="tenant-a",
        bearer_token="secret",
        resolver=lambda _host, _port: [PUBLIC_TEST_IP],
    )
    invalid_timeout = run_read_only_http_probe(**common, timeout_seconds=True)
    assert invalid_timeout["error_code"] == "unsafe_probe_url"

    malformed_response = run_read_only_http_probe(
        **common,
        executor=lambda *_args: ("200", "not-bytes"),
    )
    assert malformed_response["error_code"] == "source_unavailable"


def test_cli_malformed_config_collections_fail_closed(tmp_path: Path) -> None:
    config = _json(CONFIG)
    config["failure_budgets"] = "not-a-list"
    result, artifact, metrics = _run_cli(
        tmp_path,
        config=config,
        samples=_json(FIXTURES / "samples.json"),
        probes=_json(FIXTURES / "probes.json"),
    )
    assert result.returncode == 2
    snapshot = _json(artifact)
    assert snapshot["state"] == "unavailable"
    assert snapshot["reason_codes"] == ["payload_invalid"]
    assert 'nexus_osr_runtime_evidence_state{state="unavailable"} 1' in metrics.read_text(encoding="utf-8")


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
    result, artifact, metrics = _run_cli(
        tmp_path,
        config=_json(CONFIG),
        samples=_json(FIXTURES / "samples.json"),
        probes=_json(FIXTURES / "probes.json"),
    )
    assert result.returncode == 0, result.stderr + result.stdout
    snapshot = _json(artifact)
    assert snapshot["state"] == "ready"
    assert artifact.stat().st_size <= MAX_ARTIFACT_BYTES + 1
    assert "tenant-a" not in metrics.read_text(encoding="utf-8")
    assert set(item["state"] for item in snapshot["probes"]) <= set(ALLOWED_STATES)


def test_cli_malformed_numeric_input_writes_unavailable_evidence(tmp_path: Path) -> None:
    samples = _json(FIXTURES / "samples.json")
    first_path = next(iter(samples))
    samples[first_path]["requests"] = "unknown"
    result, artifact, metrics = _run_cli(
        tmp_path,
        config=_json(CONFIG),
        samples=samples,
        probes=_json(FIXTURES / "probes.json"),
    )
    assert result.returncode == 2, result.stderr + result.stdout
    snapshot = _json(artifact)
    assert snapshot["state"] == "unavailable"
    assert "payload_invalid" in snapshot["reason_codes"]
    summary = json.loads(result.stdout)
    assert summary["state"] == snapshot["state"]
    assert summary["reason_codes"] == snapshot["reason_codes"]
    metric_text = metrics.read_text(encoding="utf-8")
    assert 'nexus_osr_runtime_evidence_state{state="unavailable"} 1' in metric_text
    assert 'nexus_osr_runtime_evidence_state{state="ready"} 1' not in metric_text


def test_cli_oversize_snapshot_artifact_metrics_stdout_and_exit_fail_closed(tmp_path: Path) -> None:
    config = _json(CONFIG)
    original_budgets = config["failure_budgets"]
    original_probes = config["probes"]
    config["failure_budgets"] = [
        {
            **original_budgets[index % len(original_budgets)],
            "owner": f"bounded-owner-{index}",
            "rationale": "x" * 240,
        }
        for index in range(100)
    ]
    config["probes"] = [dict(original_probes[index % len(original_probes)]) for index in range(100)]

    result, artifact, metrics = _run_cli(
        tmp_path,
        config=config,
        samples=_json(FIXTURES / "samples.json"),
        probes=_json(FIXTURES / "probes.json"),
    )
    assert result.returncode == 2, result.stderr + result.stdout
    snapshot = _json(artifact)
    assert snapshot["state"] == "unavailable"
    assert snapshot["reason_codes"] == ["artifact_too_large"]
    summary = json.loads(result.stdout)
    assert summary["state"] == "unavailable"
    assert summary["reason_codes"] == ["artifact_too_large"]
    metric_text = metrics.read_text(encoding="utf-8")
    assert 'nexus_osr_runtime_evidence_state{state="unavailable"} 1' in metric_text
    assert 'nexus_osr_runtime_evidence_state{state="ready"} 1' not in metric_text


def test_deep_snapshot_finalization_returns_bounded_unavailable_evidence() -> None:
    nested: list[object] = []
    current = nested
    for _ in range(20_000):
        child: list[object] = []
        current.append(child)
        current = child

    final_snapshot, artifact_bytes = finalize_runtime_evidence(
        {"state": "ready", "deep": nested}
    )

    assert len(artifact_bytes) <= MAX_ARTIFACT_BYTES
    assert json.loads(artifact_bytes) == final_snapshot
    assert final_snapshot["state"] == "unavailable"
    assert final_snapshot["reason_codes"] == ["payload_invalid"]
    assert 'nexus_osr_runtime_evidence_state{state="unavailable"} 1' in render_prometheus_metrics(final_snapshot)
