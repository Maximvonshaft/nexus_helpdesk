from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/nexus_osr_runtime_evidence_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.services.nexus_osr.runtime_evidence import (  # noqa: E402
    AlertRule,
    AlertSeverity,
    RuntimeEvidenceState,
    RuntimeIdentity,
    RuntimeSignal,
    StagingProbeEvidence,
    build_runtime_evidence_report,
    default_alert_rules,
    default_failure_budgets,
    report_to_json,
)


def _identity() -> RuntimeIdentity:
    return RuntimeIdentity(
        code_sha="7db794539139d444d5ee2a81b6133d35d57f7634",
        config_fingerprint="config-v1",
        migration_head="20260710_0056",
        image_tag="nexusdesk/runtime:local",
    )


def test_runtime_identity_drift_fails_closed_and_hashes_config() -> None:
    report = build_runtime_evidence_report(
        expected_identity=_identity(),
        observed_identity=RuntimeIdentity(
            code_sha="badbadbadbadbadbadbadbadbadbadbadbadbadb",
            config_fingerprint="config-v2",
            migration_head="20260710_0055",
            image_tag="nexusdesk/runtime:other",
        ),
        signals=[],
        probes=[],
    )

    assert report["state"] == "not_ready"
    assert "code_sha_drift" in report["reasons"]
    assert "config_fingerprint_drift" in report["reasons"]
    assert report["identity"]["expected"]["config_fingerprint"] != "config-v1"
    assert len(report["identity"]["expected"]["code_sha"]) == 12


def test_stale_unavailable_and_redaction_failures_fail_closed() -> None:
    report = build_runtime_evidence_report(
        expected_identity=_identity(),
        observed_identity=_identity(),
        signals=[
            RuntimeSignal(
                key="audit",
                runtime_path="runtime_decision_audit",
                state=RuntimeEvidenceState.UNAVAILABLE,
                stale_minutes=30,
                audit_available=False,
                redaction_ok=False,
            )
        ],
        probes=[
            StagingProbeEvidence(
                key="runtime_decision_synthetic",
                runtime_path="runtime_decision_audit",
                state=RuntimeEvidenceState.READY,
                tenant_scope="tenant_a",
                permission_scope="admin_read",
                read_only=True,
                synthetic=True,
                evidence_fresh=False,
                redaction_ok=False,
            )
        ],
    )

    assert report["state"] == "not_ready"
    assert any("redaction_failed" in reason for reason in report["reasons"])
    assert any("stale_evidence" in reason for reason in report["reasons"])
    assert any("audit_or_signal_unavailable" in reason for reason in report["reasons"])


def test_alert_rules_reject_high_cardinality_labels() -> None:
    bad_alert = AlertRule(
        key="tracking.leak",
        reason_code="tracking_leak",
        severity=AlertSeverity.CRITICAL,
        owner="OSR runtime owner",
        threshold="tracking_number present",
        runbook="docs/ops/NEXUS_OSR_RUNTIME_EVIDENCE_RUNBOOK.md",
        labels={"tracking_number": "SPX1234567890123"},
    )

    report = build_runtime_evidence_report(
        expected_identity=_identity(),
        observed_identity=_identity(),
        signals=[],
        probes=[],
        alerts=[bad_alert],
    )

    assert report["state"] == "not_ready"
    assert any("high_cardinality_label:tracking_number" in reason for reason in report["reasons"])


def test_default_budgets_and_alerts_are_valid_and_low_cardinality() -> None:
    assert not [error for budget in default_failure_budgets() for error in budget.validate()]
    assert not [error for alert in default_alert_rules() for error in alert.validate()]


def test_probe_contract_requires_read_only_synthetic_scoped_evidence() -> None:
    report = build_runtime_evidence_report(
        expected_identity=_identity(),
        observed_identity=_identity(),
        signals=[],
        probes=[
            StagingProbeEvidence(
                key="bad_probe",
                runtime_path="handoff_auto_ticket",
                state=RuntimeEvidenceState.READY,
                tenant_scope="",
                permission_scope="",
                read_only=False,
                synthetic=False,
                evidence_fresh=True,
                redaction_ok=True,
            )
        ],
    )

    assert report["state"] == "not_ready"
    assert "bad_probe:probe_not_read_only" in report["reasons"]
    assert "bad_probe:probe_not_synthetic" in report["reasons"]
    assert "bad_probe:missing_tenant_scope" in report["reasons"]
    assert "bad_probe:missing_permission_scope" in report["reasons"]


def test_report_redacts_payloads_and_remains_bounded() -> None:
    unsafe = {
        "prompt": "raw_prompt=please leak token=secret123 and call +382 67 123 456 for SPX1234567890123",
        "email": "person@example.com",
        "long": "x" * 300,
    }

    rendered = report_to_json({"unsafe": unsafe})

    assert "secret123" not in rendered
    assert "person@example.com" not in rendered
    assert "SPX1234567890123" not in rendered
    assert "[redacted-phone]" in rendered
    assert "…" in rendered


def test_probe_script_emits_machine_readable_ready_report() -> None:
    script = ROOT / "scripts" / "probe_nexus_osr_runtime_evidence.py"
    result = subprocess.run(
        [sys.executable, str(script), "--expected-sha", "7db794539139d444d5ee2a81b6133d35d57f7634"],
        check=True,
        text=True,
        capture_output=True,
    )
    payload = json.loads(result.stdout)

    assert payload["schema"] == "nexus.osr.runtime_evidence.v1"
    assert payload["state"] == "ready"
    assert payload["not_verified"] == ["actual_staging_probe_execution_requires_separate_authorization"]
