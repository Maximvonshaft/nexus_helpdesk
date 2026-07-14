#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import scanner as scanner_module
from scanner import bounded_report, scan_artifact_files, write_report


_SAFE_RULE_RE = re.compile(r"^[a-z0-9_:.-]{2,80}$")
_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9_./-]{1,240}$")
_SAFE_EXCEPTION_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,80}$")
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_BUILD_TIME_RE = re.compile(r"^\d{8}T\d{6}Z$")
_GITHUB_ATTESTATION_ID_RE = re.compile(r"^[0-9]{1,20}$")
_GITHUB_ATTESTATION_URL_RE = re.compile(
    r"^https://github\.com/[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/"
    r"[A-Za-z0-9_.-]{1,100}/attestations/([0-9]{1,20})$"
)
_CONTROLLED_CANDIDATE_SCHEMA = "nexus.osr.controlled-candidate-manifest.v1"
_FINAL_BUNDLE_PARENT = "final-controlled-candidate"
_FINAL_BUNDLE_FILES = {
    "candidate-manifest.json",
    "controlled-candidate-manifest.json",
    "recovery-evidence.json",
    "registry-publish-receipt.json",
    "release-image-compliance-binding.json",
    "release-image-manifest.json",
}


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
    """Persist only bounded finding metadata for the isolated RC workflow."""

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
    payload = f"{rule}\0{path}\0{0}\0{value}".encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()[:16]


def _finding_key_for_value(rule: str, relative: str, value: object) -> tuple[str, str, str] | None:
    if not isinstance(value, str):
        return None
    pattern = next((pattern for name, pattern in scanner_module._PII_PATTERNS if name == rule), None)
    if pattern is None:
        return None
    match = pattern.search(value)
    if match is None:
        return None
    return (
        relative,
        f"artifact:{rule}",
        _finding_fingerprint(rule, relative, match.group(0)),
    )


def _validated_attestation(payload: object) -> tuple[str, str] | None:
    if not isinstance(payload, dict) or set(payload) != {
        "id",
        "url",
        "registry_provenance_pushed",
    }:
        return None
    attestation_id = payload.get("id")
    attestation_url = payload.get("url")
    if not isinstance(attestation_id, str) or not _GITHUB_ATTESTATION_ID_RE.fullmatch(attestation_id):
        return None
    if not isinstance(attestation_url, str):
        return None
    url_match = _GITHUB_ATTESTATION_URL_RE.fullmatch(attestation_url)
    if url_match is None or url_match.group(1) != attestation_id:
        return None
    if payload.get("registry_provenance_pushed") is not True:
        return None
    return attestation_id, attestation_url


def _load_json(path: Path) -> dict[str, object] | None:
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        return None
    return payload if isinstance(payload, dict) else None


def _validated_attestation_phone_fingerprints(root: Path, paths: list[str]) -> set[tuple[str, str, str]]:
    """Return exact phone-rule findings caused by a validated GitHub attestation ID."""

    allowed: set[tuple[str, str, str]] = set()
    for relative in sorted(set(paths))[:200]:
        payload = _load_json(root / relative)
        if payload is None or payload.get("schema") != _CONTROLLED_CANDIDATE_SCHEMA:
            continue
        attestation = _validated_attestation(payload.get("attestation"))
        if attestation is None:
            continue
        attestation_id, attestation_url = attestation
        for value in (attestation_id, attestation_url):
            key = _finding_key_for_value("phone", relative, value)
            if key is not None:
                allowed.add(key)
    return allowed


def _validated_final_bundle_metadata_fingerprints(
    root: Path,
    paths: list[str],
) -> set[tuple[str, str, str]]:
    """Bind scanner exceptions to one complete, internally consistent release bundle.

    Release metadata such as immutable image tags, UTC build timestamps and a
    GitHub attestation identifier can resemble tracking numbers or phone
    numbers. This function permits only exact scanner fingerprints derived from
    documents whose schemas, file names, source identity and safety state all
    cross-bind. It never suppresses secret rules or unrelated values.
    """

    documents: dict[str, tuple[str, dict[str, object]]] = {}
    for relative in sorted(set(paths))[:200]:
        relative_path = Path(relative)
        if relative_path.parent.name != _FINAL_BUNDLE_PARENT:
            continue
        name = relative_path.name
        if name not in _FINAL_BUNDLE_FILES or name in documents:
            return set()
        payload = _load_json(root / relative)
        if payload is None:
            return set()
        documents[name] = (relative, payload)
    if set(documents) != _FINAL_BUNDLE_FILES:
        return set()

    candidate_relative, rc = documents["candidate-manifest.json"]
    controlled_relative, controlled = documents["controlled-candidate-manifest.json"]
    recovery_relative, recovery = documents["recovery-evidence.json"]
    receipt_relative, receipt = documents["registry-publish-receipt.json"]
    binding_relative, binding = documents["release-image-compliance-binding.json"]
    assurance_relative, assurance = documents["release-image-manifest.json"]

    if set(rc) != {"schema", "release_class", "decision", "candidate", "checks", "evidence", "safety"}:
        return set()
    if rc.get("schema") != "nexus.osr.rc-test-candidate.v1" or rc.get("decision") != "RC0_TEST_DEPLOYABLE":
        return set()
    rc_candidate = rc.get("candidate")
    if not isinstance(rc_candidate, dict):
        return set()
    source = rc_candidate.get("source_sha")
    if not isinstance(source, str) or not _SHA40_RE.fullmatch(source):
        return set()
    rc_safety = rc.get("safety")
    expected_rc_safety = {
        "production_data_used": False,
        "production_network_joined": False,
        "provider_candidate_enabled": False,
        "real_outbound_enabled": False,
        "whatsapp_enabled": False,
        "speedaf_write_enabled": False,
        "operations_dispatch_enabled": False,
        "production_ready": False,
        "full_osr_automation": "NO_GO",
        "test_environment_isolated": True,
    }
    if not isinstance(rc_safety, dict) or any(rc_safety.get(key) != value for key, value in expected_rc_safety.items()):
        return set()

    if receipt.get("schema") != "nexus.osr.registry-publish-receipt.v1" or receipt.get("status") != "pass":
        return set()
    if receipt.get("source_sha") != source or receipt.get("image_pushed") is not True:
        return set()
    if receipt.get("deployment_performed") is not False:
        return set()

    if binding.get("schema_version") != "nexus_release_image_compliance_binding_v1" or binding.get("status") != "pass":
        return set()
    if binding.get("source_sha") != source or binding.get("image_pushed") is not False:
        return set()
    if binding.get("deployment_performed") is not False:
        return set()

    if assurance.get("schema_version") != "nexus_release_image_assurance_v1" or assurance.get("status") != "pass":
        return set()
    if assurance.get("source_sha") != source:
        return set()
    if any(int(assurance.get(key) or 0) != 0 for key in ("critical_count", "high_count", "unresolved_license_count")):
        return set()
    if assurance.get("image_pushed") is not False or assurance.get("deployment_performed") is not False:
        return set()

    if recovery.get("schema_version") != "nexus_postgres_recovery_qualification_v1" or recovery.get("status") != "pass":
        return set()
    if recovery.get("source_sha") != source or recovery.get("reasons") != []:
        return set()
    if recovery.get("production_data_used") is not False or recovery.get("production_mutation_performed") is not False:
        return set()

    if set(controlled) != {
        "schema",
        "status",
        "decision",
        "release_class",
        "generated_at",
        "candidate",
        "attestation",
        "evidence",
        "safety",
    }:
        return set()
    if controlled.get("schema") != _CONTROLLED_CANDIDATE_SCHEMA or controlled.get("status") != "pass":
        return set()
    if controlled.get("decision") != "CONTROLLED_SERVER_CANDIDATE_PUBLISHED":
        return set()
    controlled_candidate = controlled.get("candidate")
    if not isinstance(controlled_candidate, dict) or controlled_candidate.get("source_sha") != source:
        return set()
    expected_controlled_safety = {
        "production_ready": False,
        "full_osr_automation": "NO_GO",
        "issue_533_go": False,
        "deployment_performed": False,
        "production_data_used": False,
        "provider_enabled": False,
        "real_outbound_enabled": False,
        "whatsapp_enabled": False,
        "speedaf_writes_enabled": False,
        "operations_dispatch_enabled": False,
        "external_effects_authorized": False,
    }
    controlled_safety = controlled.get("safety")
    if not isinstance(controlled_safety, dict) or any(
        controlled_safety.get(key) != value for key, value in expected_controlled_safety.items()
    ):
        return set()

    expected_tag = f"nexusdesk/helpdesk:rc-test-{source}"
    app_version = receipt.get("app_version")
    build_time = receipt.get("build_time")
    evaluated_on = binding.get("evaluated_on")
    generated_at = controlled.get("generated_at")
    if rc_candidate.get("image_tag") != expected_tag:
        return set()
    if receipt.get("embedded_image_tag") != expected_tag or controlled_candidate.get("embedded_image_tag") != expected_tag:
        return set()
    if app_version != f"controlled-{source[:12]}" or controlled_candidate.get("app_version") != app_version:
        return set()
    if not isinstance(build_time, str) or not _BUILD_TIME_RE.fullmatch(build_time):
        return set()
    if controlled_candidate.get("build_time") != build_time:
        return set()
    try:
        build_dt = datetime.strptime(build_time, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        evaluated_date = datetime.strptime(str(evaluated_on), "%Y-%m-%d").date()
        generated_dt = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return set()
    if generated_dt.tzinfo is None or generated_dt.utcoffset() != timezone.utc.utcoffset(generated_dt):
        return set()
    if build_dt.date() != evaluated_date or generated_dt.date() != evaluated_date:
        return set()

    attestation = _validated_attestation(controlled.get("attestation"))
    if attestation is None:
        return set()
    attestation_id, attestation_url = attestation

    allowed: set[tuple[str, str, str]] = set()
    technical_values = (
        ("tracking", candidate_relative, expected_tag),
        ("tracking", receipt_relative, app_version),
        ("tracking", receipt_relative, build_time),
        ("tracking", receipt_relative, expected_tag),
        ("phone", binding_relative, str(evaluated_on)),
        ("tracking", controlled_relative, app_version),
        ("tracking", controlled_relative, build_time),
        ("tracking", controlled_relative, expected_tag),
        ("tracking", controlled_relative, str(generated_at)),
        ("phone", controlled_relative, attestation_id),
        ("phone", controlled_relative, attestation_url),
    )
    for rule, relative, value in technical_values:
        key = _finding_key_for_value(rule, relative, value)
        if key is None:
            return set()
        allowed.add(key)

    # Keep all six exact file identities referenced so future edits cannot
    # accidentally remove a cross-binding document without failing closed.
    _ = (recovery_relative, assurance_relative)
    return allowed


def _suppress_validated_attestation_phone_findings(
    *, root: Path, paths: list[str], findings: list[object]
) -> tuple[list[object], int]:
    allowed = _validated_attestation_phone_fingerprints(root, paths)
    return _filter_allowed_findings(findings, allowed)


def _suppress_validated_final_bundle_metadata_findings(
    *, root: Path, paths: list[str], findings: list[object]
) -> tuple[list[object], int]:
    allowed = _validated_attestation_phone_fingerprints(root, paths)
    allowed.update(_validated_final_bundle_metadata_fingerprints(root, paths))
    return _filter_allowed_findings(findings, allowed)


def _filter_allowed_findings(
    findings: list[object],
    allowed: set[tuple[str, str, str]],
) -> tuple[list[object], int]:
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
        findings, suppressed = _suppress_validated_final_bundle_metadata_findings(
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
