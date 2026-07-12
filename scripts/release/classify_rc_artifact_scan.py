#!/usr/bin/env python3
"""Convert RC artifact-scan results into a bounded, upload-safe failure receipt."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

SAFE_RULE_RE = re.compile(r"^[a-z0-9_:.-]{2,80}$")
SAFE_PATH_RE = re.compile(r"^artifacts/rc-test/[A-Za-z0-9._-]{1,120}$")
SAFE_FINGERPRINT_RE = re.compile(r"^[a-f0-9]{16}$")
MAX_FINDINGS = 10


def _load_object(path: Path) -> dict[str, Any] | None:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 64 * 1024:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _safe_findings(report: dict[str, Any]) -> list[dict[str, str]]:
    raw_findings = report.get("findings")
    if not isinstance(raw_findings, list):
        return []
    output: list[dict[str, str]] = []
    for raw in raw_findings[:MAX_FINDINGS]:
        if not isinstance(raw, dict):
            continue
        rule = raw.get("rule")
        path = raw.get("path")
        fingerprint = raw.get("fingerprint")
        if (
            isinstance(rule, str)
            and isinstance(path, str)
            and isinstance(fingerprint, str)
            and SAFE_RULE_RE.fullmatch(rule)
            and SAFE_PATH_RE.fullmatch(path)
            and SAFE_FINGERPRINT_RE.fullmatch(fingerprint)
        ):
            output.append({"rule": rule, "path": path, "fingerprint": fingerprint})
    return output


def classify(report_path: Path, summary_path: Path, exit_code: int) -> dict[str, Any]:
    summary = _load_object(summary_path) or {
        "schema": "nexus.osr.rc-test-failure-summary.v1",
        "status": "failed",
        "stage": "artifact-scan",
        "exit_code": int(exit_code),
        "reason_code": "evidence_validation_or_scan_failed",
        "service_states": {},
    }
    report = _load_object(report_path)
    if report and report.get("schema_version") == "nexus_security_artifact_scan_v1":
        findings = _safe_findings(report)
        finding_count = report.get("finding_count")
        scanned_files = report.get("scanned_files")
        if isinstance(finding_count, int) and finding_count > 0:
            summary["reason_code"] = "artifact_scan_findings"
            summary["scan_finding_count"] = min(finding_count, 200)
            if isinstance(scanned_files, int) and scanned_files >= 0:
                summary["scan_scanned_files"] = min(scanned_files, 200)
            if findings:
                summary["scan_findings"] = findings
    summary["schema"] = "nexus.osr.rc-test-failure-summary.v1"
    summary["status"] = "failed"
    summary["stage"] = "artifact-scan"
    summary["exit_code"] = int(exit_code)
    states = summary.get("service_states")
    if not isinstance(states, dict):
        summary["service_states"] = {}
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--exit-code", type=int, required=True)
    args = parser.parse_args()
    classify(args.report, args.summary, args.exit_code)
    print("RC_ARTIFACT_SCAN_CLASSIFIED=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
