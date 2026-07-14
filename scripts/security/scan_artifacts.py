#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from scanner import bounded_report, scan_artifact_files, write_report


_SAFE_RULE_RE = re.compile(r"^[a-z0-9_:.-]{2,80}$")
_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9_./-]{1,240}$")
_SAFE_EXCEPTION_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,80}$")
_GITHUB_ATTESTATION_ID_RE = re.compile(r"^[0-9]{1,20}$")
_GITHUB_ATTESTATION_URL_RE = re.compile(
    r"^https://github\.com/[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/"
    r"[A-Za-z0-9_.-]{1,100}/attestations/([0-9]{1,20})$"
)
_CONTROLLED_CANDIDATE_SCHEMA = "nexus.osr.controlled-candidate-manifest.v1"


def _rc_root_for_output(output: Path) -> Path | None:
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
    return rc_root


def _rc_context(output: Path) -> tuple[Path, object] | None:
    """Load the strict RC scanner only for the exact isolated evidence root."""

    rc_root = _rc_root_for_output(output)
    if rc_root is None:
        return None

    release_dir = Path(__file__).resolve().parents[1] / "release"
    if str(release_dir) not in sys.path:
        sys.path.insert(0, str(release_dir))
    import scan_rc_test_artifacts

    return rc_root, scan_rc_test_artifacts


def _write_rc_runtime_failure(rc_root: Path | None, exc: BaseException) -> None:
    if rc_root is None:
        return
    exception_type = type(exc).__name__
    if not _SAFE_EXCEPTION_RE.fullmatch(exception_type):
        exception_type = "UnknownError"
    payload = {
        "schema": "nexus.osr.rc-test-failure-summary.v1",
        "status": "failed",
        "stage": "artifact-scan",
        "exit_code": 1,
        "reason_code": "artifact_scan_runtime_error",
        "service_states": {},
        "scanner_exception_type": exception_type,
    }
    (rc_root / "failure-summary.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_rc_failure_summary(output: Path, findings: list[object]) -> None:
    """Persist only bounded finding metadata for the isolated RC workflow.

    This remains a defensive fallback. The strict RC scanner is preferred when
    present and performs fingerprint-level suppression only for validated
    technical metadata. Neither path records matched values or raw contents.
    """

    rc_root = _rc_root_for_output(output)
    if rc_root is None or not findings:
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


def _finding_fingerprint(rule: str, path: str, value: str) -> str:
    payload = f"{rule}\0{path}\00\0{value}".encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()[:16]


def _validated_attestation_phone_fingerprints(root: Path, paths: list[str]) -> set[tuple[str, str, str]]:
    """Return exact scanner findings caused by a validated GitHub attestation ID.

    GitHub's persisted attestation ID is a numeric technical identifier. The
    generic PII detector intentionally treats long digit strings as phone-like.
    Suppression is permitted only for the exact controlled-candidate schema,
    the exact three-field attestation object, a numeric GitHub ID, and a URL
    whose terminal identifier is identical. Secret-pattern findings are never
    suppressed.
    """

    allowed: set[tuple[str, str, str]] = set()
    for relative in sorted(set(paths))[:200]:
        path = root / relative
        try:
            if not path.is_file() or path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
            continue
        if not isinstance(payload, dict) or payload.get("schema") != _CONTROLLED_CANDIDATE_SCHEMA:
            continue
        attestation = payload.get("attestation")
        if not isinstance(attestation, dict) or set(attestation) != {
            "id",
            "url",
            "registry_provenance_pushed",
        }:
            continue
        attestation_id = attestation.get("id")
        attestation_url = attestation.get("url")
        if not isinstance(attestation_id, str) or not _GITHUB_ATTESTATION_ID_RE.fullmatch(attestation_id):
            continue
        if not isinstance(attestation_url, str):
            continue
        url_match = _GITHUB_ATTESTATION_URL_RE.fullmatch(attestation_url)
        if url_match is None or url_match.group(1) != attestation_id:
            continue
        if attestation.get("registry_provenance_pushed") is not True:
            continue
        fingerprint = _finding_fingerprint("phone", relative, attestation_id)
        allowed.add((relative, "artifact:phone", fingerprint))
    return allowed


def _suppress_validated_attestation_phone_findings(
    *, root: Path, paths: list[str], findings: list[object]
) -> tuple[list[object], int]:
    allowed = _validated_attestation_phone_fingerprints(root, paths)
    if not allowed:
        return findings, 0
    remaining: list[object] = []
    suppressed = 0
    for finding in findings:
        key = (
            str(getattr(finding, "path", "")),
            str(getattr(finding, "rule", "")),
            str(getattr(finding, "fingerprint", "")),
        )
        if key in allowed:
            suppressed += 1
        else:
            remaining.append(finding)
    return remaining, suppressed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--output", required=True)
    parser.add_argument("paths", nargs="+")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    output = Path(args.output)
    rc_root = _rc_root_for_output(output)
    try:
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
        findings, suppressed = _suppress_validated_attestation_phone_findings(
            root=root,
            paths=args.paths,
            findings=findings,
        )
        write_report(
            output,
            bounded_report(
                schema="nexus_security_artifact_scan_v1",
                findings=findings,
                scanned_files=len(args.paths),
                suppressed_count=suppressed,
            ),
        )
        _write_rc_failure_summary(output, findings)
        return 1 if findings else 0
    except Exception as exc:
        _write_rc_runtime_failure(rc_root, exc)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
