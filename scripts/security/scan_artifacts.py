#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from scanner import bounded_report, scan_artifact_files, write_report


_SAFE_RULE_RE = re.compile(r"^[a-z0-9_:.-]{2,80}$")
_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9_./-]{1,240}$")


def _write_rc_failure_summary(output: Path, findings: list[object]) -> None:
    """Persist only bounded finding metadata for the isolated RC workflow.

    The main artifact report remains the source of truth. This companion summary
    exists only when the globally supplied RC evidence root exactly matches the
    report parent. It records no matched values, source lines, credentials, PII,
    or raw evidence content.
    """

    raw_rc_root = (os.getenv("RC_EVIDENCE_DIR") or "").strip()
    if not raw_rc_root or not findings:
        return
    try:
        rc_root = Path(raw_rc_root).resolve(strict=True)
        report_parent = output.resolve().parent
    except OSError:
        return
    if report_parent != rc_root or rc_root.name != "rc-test":
        return

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
    findings = scan_artifact_files(root, args.paths)
    write_report(
        output,
        bounded_report(schema="nexus_security_artifact_scan_v1", findings=findings, scanned_files=len(args.paths)),
    )
    _write_rc_failure_summary(output, findings)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
