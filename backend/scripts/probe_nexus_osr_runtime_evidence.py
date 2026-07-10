from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.nexus_osr.runtime_evidence import (  # noqa: E402
    RuntimeEvidenceState,
    RuntimeIdentity,
    RuntimeSignal,
    StagingProbeEvidence,
    build_runtime_evidence_report,
    report_to_json,
)


def _load_snapshot(path: str | None) -> dict[str, object]:
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _identity(payload: dict[str, object], fallback_sha: str) -> RuntimeIdentity:
    return RuntimeIdentity(
        code_sha=str(payload.get("code_sha") or fallback_sha),
        config_fingerprint=str(payload.get("config_fingerprint") or "synthetic-default"),
        migration_head=str(payload.get("migration_head") or "20260710_0056"),
        image_tag=str(payload.get("image_tag") or "synthetic-local"),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build bounded Nexus OSR runtime evidence from a redacted synthetic snapshot.")
    parser.add_argument("--snapshot", help="Optional redacted JSON snapshot. No production data or raw payloads.", default=None)
    parser.add_argument("--expected-sha", default="local", help="Expected code SHA or local placeholder.")
    parser.add_argument("--output", default=None, help="Optional output JSON path.")
    args = parser.parse_args(argv)

    snapshot = _load_snapshot(args.snapshot)
    expected = _identity(dict(snapshot.get("expected_identity") or {}), args.expected_sha)
    observed = _identity(dict(snapshot.get("observed_identity") or {}), args.expected_sha)

    signals = [
        RuntimeSignal(
            key="runtime_audit",
            runtime_path="runtime_decision_audit",
            state=RuntimeEvidenceState.READY,
            failure_rate=0.0,
            audit_available=True,
            redaction_ok=True,
        ),
        RuntimeSignal(
            key="operator_queue",
            runtime_path="handoff_auto_ticket",
            state=RuntimeEvidenceState.READY,
            failure_rate=0.0,
            queue_backlog=0,
        ),
        RuntimeSignal(
            key="tracking_truth",
            runtime_path="tracking_truth_layer",
            state=RuntimeEvidenceState.READY,
            failure_rate=0.0,
        ),
    ]
    probes = [
        StagingProbeEvidence(
            key="runtime_decision_synthetic",
            runtime_path="runtime_decision_audit",
            state=RuntimeEvidenceState.READY,
            tenant_scope="synthetic_tenant",
            permission_scope="admin_read",
            read_only=True,
            synthetic=True,
            evidence_fresh=True,
            redaction_ok=True,
            details={"source": "synthetic_fixture", "customer_text": "redacted"},
        ),
        StagingProbeEvidence(
            key="dispatch_synthetic",
            runtime_path="operations_dispatch_outbox",
            state=RuntimeEvidenceState.READY,
            tenant_scope="synthetic_tenant",
            permission_scope="operator_read",
            read_only=True,
            synthetic=True,
            evidence_fresh=True,
            redaction_ok=True,
            details={"external_send": "not_performed"},
        ),
    ]
    report = build_runtime_evidence_report(
        expected_identity=expected,
        observed_identity=observed,
        signals=signals,
        probes=probes,
    )
    rendered = report_to_json(report)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0 if report["state"] in {"ready", "degraded"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
