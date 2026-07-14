#!/usr/bin/env python3
"""Scan the final controlled-candidate bundle without misclassifying bound machine metadata."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

SECURITY_DIR = Path(__file__).resolve().parents[1] / "security"
if str(SECURITY_DIR) not in sys.path:
    sys.path.insert(0, str(SECURITY_DIR))

from scanner import Finding, bounded_report, scan_artifact_files, write_report

MAX_INPUT_BYTES = 2 * 1024 * 1024
EXPECTED_FILES = {
    "candidate-manifest.json",
    "release-image-manifest.json",
    "release-image-compliance-binding.json",
    "registry-publish-receipt.json",
    "recovery-evidence.json",
    "controlled-candidate-manifest.json",
}
EVIDENCE_FILES = {
    "rc_candidate_manifest": "candidate-manifest.json",
    "release_image_manifest": "release-image-manifest.json",
    "release_image_compliance_binding": "release-image-compliance-binding.json",
    "recovery_evidence": "recovery-evidence.json",
    "registry_publish_receipt": "registry-publish-receipt.json",
}

_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_MIGRATION = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_BUILD_TIME = re.compile(r"^\d{8}T\d{6}Z$")
_APP_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,79}$")
_LOCAL_IMAGE_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*:[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_IMAGE_NAME = re.compile(r"^ghcr\.io/[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._/-]*$")
_DIGEST_REFERENCE = re.compile(r"^[A-Za-z0-9._/-]+@sha256:[0-9a-f]{64}$")
_SAFE_METADATA = re.compile(r"^[A-Za-z0-9@._:/+\-]{1,160}$")
_ATTESTATION_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")
_ATTESTATION_URL = re.compile(
    r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/attestations/([A-Za-z0-9_.:-]{1,200})$"
)
_GENERATED_AT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")

_FINAL_TOP_KEYS = {
    "schema",
    "status",
    "decision",
    "release_class",
    "generated_at",
    "candidate",
    "attestation",
    "evidence",
    "safety",
}
_FINAL_CANDIDATE_KEYS = {
    "source_sha",
    "frontend_build_sha",
    "migration_revision",
    "build_time",
    "app_version",
    "embedded_image_tag",
    "local_image_id",
    "registry_pull_image_id",
    "registry_image",
    "registry_digest",
    "registry_reference",
    "config_profile",
    "config_digest",
    "postgres_image_digest",
    "nginx_image_digest",
}
_FINAL_ATTESTATION_KEYS = {"id", "url", "registry_provenance_pushed"}
_FINAL_SAFETY = {
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


def _fingerprint(rule: str, path: str, reason: str) -> str:
    payload = f"{rule}\0{path}\0{reason}".encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()[:16]


def _finding(rule: str, path: str, reason: str) -> Finding:
    return Finding(rule=rule, path=path, line=0, fingerprint=_fingerprint(rule, path, reason))


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return "sha256:" + value.hexdigest()


def _load_object(root: Path, name: str, findings: list[Finding]) -> dict[str, Any] | None:
    path = root / name
    if path.is_symlink() or not path.is_file():
        findings.append(_finding("controlled_candidate_file_invalid", name, "missing_or_symlink"))
        return None
    try:
        size = path.stat().st_size
    except OSError:
        findings.append(_finding("controlled_candidate_file_invalid", name, "stat_failed"))
        return None
    if size > MAX_INPUT_BYTES:
        findings.append(_finding("controlled_candidate_file_invalid", name, "too_large"))
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        findings.append(_finding("controlled_candidate_json_invalid", name, "parse_failed"))
        return None
    if not isinstance(payload, dict):
        findings.append(_finding("controlled_candidate_json_invalid", name, "object_required"))
        return None
    return payload


def _replace_validated_string(
    payload: dict[str, Any],
    path: tuple[str, ...],
    validator: Callable[[str], bool],
    file_name: str,
    findings: list[Finding],
) -> None:
    current: Any = payload
    for part in path[:-1]:
        if not isinstance(current, dict) or not isinstance(current.get(part), dict):
            findings.append(_finding("controlled_candidate_metadata_invalid", file_name, ".".join(path)))
            return
        current = current[part]
    key = path[-1]
    if not isinstance(current, dict):
        findings.append(_finding("controlled_candidate_metadata_invalid", file_name, ".".join(path)))
        return
    value = current.get(key)
    if not isinstance(value, str) or not validator(value):
        findings.append(_finding("controlled_candidate_metadata_invalid", file_name, ".".join(path)))
        return
    current[key] = "validated-machine-metadata"


def _valid_build_time(value: str) -> bool:
    if not _BUILD_TIME.fullmatch(value):
        return False
    try:
        datetime.strptime(value, "%Y%m%dT%H%M%SZ")
    except ValueError:
        return False
    return True


def _valid_generated_at(value: str) -> bool:
    if not _GENERATED_AT.fullmatch(value):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _valid_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))


def _strict_keys(value: object, expected: set[str]) -> bool:
    return isinstance(value, dict) and set(value) == expected


def _valid_final_manifest(payload: dict[str, Any], root: Path, findings: list[Finding]) -> bool:
    name = "controlled-candidate-manifest.json"
    if not _strict_keys(payload, _FINAL_TOP_KEYS):
        findings.append(_finding("controlled_candidate_manifest_invalid", name, "top_level_keys"))
        return False
    if (
        payload.get("schema") != "nexus.osr.controlled-candidate-manifest.v1"
        or payload.get("status") != "pass"
        or payload.get("decision") != "CONTROLLED_SERVER_CANDIDATE_PUBLISHED"
        or payload.get("release_class") != "controlled_server_deployment"
    ):
        findings.append(_finding("controlled_candidate_manifest_invalid", name, "identity"))
        return False

    generated_at = payload.get("generated_at")
    if not isinstance(generated_at, str) or not _valid_generated_at(generated_at):
        findings.append(_finding("controlled_candidate_manifest_invalid", name, "generated_at"))
        return False

    candidate = payload.get("candidate")
    if not _strict_keys(candidate, _FINAL_CANDIDATE_KEYS):
        findings.append(_finding("controlled_candidate_manifest_invalid", name, "candidate_keys"))
        return False
    assert isinstance(candidate, dict)
    source = candidate.get("source_sha")
    frontend = candidate.get("frontend_build_sha")
    local_id = candidate.get("local_image_id")
    pulled_id = candidate.get("registry_pull_image_id")
    image = candidate.get("registry_image")
    digest = candidate.get("registry_digest")
    reference = candidate.get("registry_reference")
    valid_candidate = (
        isinstance(source, str)
        and bool(_SHA40.fullmatch(source))
        and frontend == source
        and isinstance(candidate.get("migration_revision"), str)
        and bool(_MIGRATION.fullmatch(candidate["migration_revision"]))
        and isinstance(candidate.get("build_time"), str)
        and _valid_build_time(candidate["build_time"])
        and isinstance(candidate.get("app_version"), str)
        and bool(_APP_VERSION.fullmatch(candidate["app_version"]))
        and isinstance(candidate.get("embedded_image_tag"), str)
        and bool(_LOCAL_IMAGE_TAG.fullmatch(candidate["embedded_image_tag"]))
        and isinstance(local_id, str)
        and bool(_SHA256.fullmatch(local_id))
        and pulled_id == local_id
        and isinstance(image, str)
        and bool(_IMAGE_NAME.fullmatch(image))
        and isinstance(digest, str)
        and bool(_SHA256.fullmatch(digest))
        and reference == f"{image}@{digest}"
        and isinstance(candidate.get("config_profile"), str)
        and bool(_SAFE_METADATA.fullmatch(candidate["config_profile"]))
        and isinstance(candidate.get("config_digest"), str)
        and bool(_SHA256.fullmatch(candidate["config_digest"]))
        and isinstance(candidate.get("postgres_image_digest"), str)
        and bool(_DIGEST_REFERENCE.fullmatch(candidate["postgres_image_digest"]))
        and isinstance(candidate.get("nginx_image_digest"), str)
        and bool(_DIGEST_REFERENCE.fullmatch(candidate["nginx_image_digest"]))
    )
    if not valid_candidate:
        findings.append(_finding("controlled_candidate_manifest_invalid", name, "candidate_values"))
        return False

    attestation = payload.get("attestation")
    if not _strict_keys(attestation, _FINAL_ATTESTATION_KEYS):
        findings.append(_finding("controlled_candidate_manifest_invalid", name, "attestation_keys"))
        return False
    assert isinstance(attestation, dict)
    attestation_id = attestation.get("id")
    attestation_url = attestation.get("url")
    url_match = _ATTESTATION_URL.fullmatch(attestation_url) if isinstance(attestation_url, str) else None
    if (
        not isinstance(attestation_id, str)
        or not _ATTESTATION_ID.fullmatch(attestation_id)
        or url_match is None
        or url_match.group(1) != attestation_id
        or attestation.get("registry_provenance_pushed") is not True
    ):
        findings.append(_finding("controlled_candidate_manifest_invalid", name, "attestation_values"))
        return False

    evidence = payload.get("evidence")
    if not _strict_keys(evidence, set(EVIDENCE_FILES)):
        findings.append(_finding("controlled_candidate_manifest_invalid", name, "evidence_keys"))
        return False
    assert isinstance(evidence, dict)
    for key, file_name in EVIDENCE_FILES.items():
        entry = evidence.get(key)
        if not _strict_keys(entry, {"path", "sha256"}):
            findings.append(_finding("controlled_candidate_manifest_invalid", name, f"evidence_entry:{key}"))
            return False
        assert isinstance(entry, dict)
        if entry.get("path") != file_name or entry.get("sha256") != _digest(root / file_name):
            findings.append(_finding("controlled_candidate_manifest_invalid", name, f"evidence_binding:{key}"))
            return False

    if payload.get("safety") != _FINAL_SAFETY:
        findings.append(_finding("controlled_candidate_manifest_invalid", name, "safety"))
        return False
    return True


def _sanitize_sources(
    root: Path,
    payloads: dict[str, dict[str, Any]],
    findings: list[Finding],
) -> dict[str, dict[str, Any]]:
    sanitized = copy.deepcopy(payloads)

    candidate = sanitized["candidate-manifest.json"]
    if candidate.get("schema") != "nexus.osr.rc-test-candidate.v1":
        findings.append(_finding("controlled_candidate_source_invalid", "candidate-manifest.json", "schema"))
    _replace_validated_string(
        candidate,
        ("candidate", "image_tag"),
        lambda value: bool(_LOCAL_IMAGE_TAG.fullmatch(value)),
        "candidate-manifest.json",
        findings,
    )

    assurance = sanitized["release-image-manifest.json"]
    if assurance.get("schema_version") != "nexus_release_image_assurance_v1":
        findings.append(_finding("controlled_candidate_source_invalid", "release-image-manifest.json", "schema"))

    binding = sanitized["release-image-compliance-binding.json"]
    if binding.get("schema_version") != "nexus_release_image_compliance_binding_v1":
        findings.append(
            _finding("controlled_candidate_source_invalid", "release-image-compliance-binding.json", "schema")
        )
    _replace_validated_string(
        binding,
        ("evaluated_on",),
        _valid_date,
        "release-image-compliance-binding.json",
        findings,
    )

    receipt = sanitized["registry-publish-receipt.json"]
    if receipt.get("schema") != "nexus.osr.registry-publish-receipt.v1":
        findings.append(_finding("controlled_candidate_source_invalid", "registry-publish-receipt.json", "schema"))
    for field, validator in (
        ("app_version", lambda value: bool(_APP_VERSION.fullmatch(value))),
        ("build_time", _valid_build_time),
        ("embedded_image_tag", lambda value: bool(_LOCAL_IMAGE_TAG.fullmatch(value))),
    ):
        _replace_validated_string(
            receipt,
            (field,),
            validator,
            "registry-publish-receipt.json",
            findings,
        )

    recovery = sanitized["recovery-evidence.json"]
    if recovery.get("schema_version") != "nexus_postgres_recovery_qualification_v1":
        findings.append(_finding("controlled_candidate_source_invalid", "recovery-evidence.json", "schema"))

    final = sanitized["controlled-candidate-manifest.json"]
    if _valid_final_manifest(payloads["controlled-candidate-manifest.json"], root, findings):
        for path, validator in (
            (("generated_at",), _valid_generated_at),
            (("candidate", "build_time"), _valid_build_time),
            (("candidate", "app_version"), lambda value: bool(_APP_VERSION.fullmatch(value))),
            (("candidate", "embedded_image_tag"), lambda value: bool(_LOCAL_IMAGE_TAG.fullmatch(value))),
            (("attestation", "id"), lambda value: bool(_ATTESTATION_ID.fullmatch(value))),
            (("attestation", "url"), lambda value: _ATTESTATION_URL.fullmatch(value) is not None),
        ):
            _replace_validated_string(final, path, validator, "controlled-candidate-manifest.json", findings)

    return sanitized


def scan_controlled_candidate_artifacts(root: Path, output: Path) -> int:
    root = root.resolve()
    output = output.resolve()
    findings: list[Finding] = []
    if not root.is_dir() or root.is_symlink():
        findings.append(_finding("controlled_candidate_root_invalid", str(root), "missing_or_symlink"))
        write_report(
            output,
            bounded_report(
                schema="nexus_controlled_candidate_artifact_scan_v1",
                findings=findings,
                scanned_files=0,
            ),
        )
        return 1

    actual_json = {path.name for path in root.glob("*.json") if path.resolve() != output}
    if actual_json != EXPECTED_FILES:
        findings.append(_finding("controlled_candidate_file_set_invalid", root.name, "unexpected_or_missing_json"))

    payloads: dict[str, dict[str, Any]] = {}
    for name in sorted(EXPECTED_FILES):
        payload = _load_object(root, name, findings)
        if payload is not None:
            payloads[name] = payload

    if set(payloads) == EXPECTED_FILES:
        sanitized = _sanitize_sources(root, payloads, findings)
        with tempfile.TemporaryDirectory(prefix="controlled-candidate-scan-") as directory:
            temporary_root = Path(directory)
            for name, payload in sanitized.items():
                (temporary_root / name).write_text(
                    json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
                    encoding="utf-8",
                )
            findings.extend(scan_artifact_files(temporary_root, sorted(EXPECTED_FILES)))

    write_report(
        output,
        bounded_report(
            schema="nexus_controlled_candidate_artifact_scan_v1",
            findings=findings,
            scanned_files=len(payloads),
        ),
    )
    return 1 if findings else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    return scan_controlled_candidate_artifacts(args.root, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
