#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from scanner import bounded_report, scan_artifact_files, write_report


_SAFE_RULE_RE = re.compile(r"^[a-z0-9_:.-]{2,80}$")
_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9_./-]{1,240}$")


def _rc_context(output: Path) -> tuple[Path, object] | None:
    """Load the strict RC scanner only for the exact isolated evidence root."""

    raw_rc_root = (os.getenv("RC_EVIDENCE_DIR") or "").strip()
    if not raw_rc_root:
        return None
    try:
        rc_root = Path(raw_rc_root).resolve(strict=True)
        report_parent = output.resolve().parent
    except OSError:
        return None
    if report_parent != rc_root or rc_root.name != "rc-test":
        return None

    release_dir = Path(__file__).resolve().parents[1] / "release"
    if str(release_dir) not in sys.path:
        sys.path.insert(0, str(release_dir))
    import scan_rc_test_artifacts

    return rc_root, scan_rc_test_artifacts


def _write_rc_failure_summary(output: Path, findings: list[object]) -> None:
    """Persist only bounded finding metadata for the isolated RC workflow.

    This remains a defensive fallback. The strict RC scanner is preferred when
    present and performs fingerprint-level suppression only for validated
    technical metadata. Neither path records matched values or raw contents.
    """

    context = _rc_context(output)
    if context is None or not findings:
        return
    rc_root, _ = context

    rules: list[str] = []
    paths: list[str] = []
    for finding in findings[:20]:
        rule = str(getattr(finding, "rule", ""))
        path = str(getattr(finding, "path", ""))
        if _SAFE_RULE_RE.fullmatch(rule) and rule not in rules:
            rules.append(rule)
        if _SAFE_PATH_RE.fullmatch(path) and path not in paths:
            paths.append(path)

    payload = {
        "schema": "nexus.osr.rc-test-failure-summary.v1",
        "status": "failed",
        "stage": "artifact-scan",
        "exit_code": 1,
        "reason_code": "artifact_scan_findings",
        "service_states": {},
        "finding_count": len(findings),
        "finding_rules": rules[:10],
        "finding_paths": paths[:10],
    }
    (rc_root / "failure-summary.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--output", required=True)
    parser.add_argument("paths", nargs="+")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    output = Path(args.output)
    context = _rc_context(output)
    if context is not None:
        rc_root, rc_scanner = context
        findings, suppressed = rc_scanner.scan_rc_artifact_files(root, args.paths)
        write_report(
            output,
            bounded_report(
                schema="nexus_security_artifact_scan_v1",
                findings=findings,
                scanned_files=len(args.paths),
                suppressed_count=suppressed,
            ),
        )
        if findings:
            rc_scanner._write_failure_summary(root, findings)
            return 1
        failure_summary = rc_root / "failure-summary.json"
        if failure_summary.is_file():
            failure_summary.unlink()
        print(f"RC_ARTIFACT_SCAN_VALID=true files={len(args.paths)} technical_pii_suppressed={suppressed}")
        return 0

    findings = scan_artifact_files(root, args.paths)
    write_report(
        output,
        bounded_report(schema="nexus_security_artifact_scan_v1", findings=findings, scanned_files=len(args.paths)),
    )
    _write_rc_failure_summary(output, findings)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
