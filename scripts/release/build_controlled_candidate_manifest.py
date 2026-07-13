#!/usr/bin/env python3
"""Build and validate the immutable controlled-candidate release manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_BYTES = 512 * 1024
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_IMAGE_NAME = re.compile(r"^ghcr\.io/[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._/-]*$")
_MIGRATION = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_ATTESTATION_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")


class ManifestError(ValueError):
    pass


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ManifestError(f"input_invalid:{path.name}")
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ManifestError(f"input_too_large:{path.name}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"json_invalid:{path.name}") from exc
    if not isinstance(payload, dict):
        raise ManifestError(f"json_object_required:{path.name}")
    return payload


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _sha40(value: str, reason: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA40.fullmatch(normalized):
        raise ManifestError(reason)
    return normalized


def _sha256(value: str, reason: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256.fullmatch(normalized):
        raise ManifestError(reason)
    return normalized


def _migration(value: str, reason: str) -> str:
    normalized = str(value or "").strip()
    if not _MIGRATION.fullmatch(normalized):
        raise ManifestError(reason)
    return normalized


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ManifestError(reason)


def build_manifest(
    *,
    source_sha: str,
    registry_image: str,
    registry_digest: str,
    local_image_id: str,
    pulled_image_id: str,
    migration_head: str,
    frontend_sha: str,
    attestation_id: str,
    attestation_url: str,
    rc_manifest_path: Path,
    release_image_manifest_path: Path,
    compliance_binding_path: Path,
    recovery_evidence_path: Path,
    publish_receipt_path: Path,
    output_path: Path,
) -> int:
    source = _sha40(source_sha, "source_sha_invalid")
    frontend = _sha40(frontend_sha, "frontend_sha_invalid")
    migration = _migration(migration_head, "migration_head_invalid")
    local_image = _sha256(local_image_id, "local_image_id_invalid")
    pulled_image = _sha256(pulled_image_id, "pulled_image_id_invalid")
    digest = _sha256(registry_digest, "registry_digest_invalid")
    image_name = str(registry_image or "").strip().lower()
    if not _IMAGE_NAME.fullmatch(image_name):
        raise ManifestError("registry_image_invalid")
    if not _ATTESTATION_ID.fullmatch(str(attestation_id or "").strip()):
        raise ManifestError("attestation_id_invalid")
    attestation_url_value = str(attestation_url or "").strip()
    if not (
        1 <= len(attestation_url_value) <= 500
        and attestation_url_value.startswith("https://github.com/")
        and all(ch not in attestation_url_value for ch in "\r\n\x00")
    ):
        raise ManifestError("attestation_url_invalid")

    _require(frontend == source, "frontend_source_mismatch")
    _require(local_image == pulled_image, "registry_pull_image_id_mismatch")

    rc = _load(rc_manifest_path)
    assurance = _load(release_image_manifest_path)
    binding = _load(compliance_binding_path)
    recovery = _load(recovery_evidence_path)
    receipt = _load(publish_receipt_path)

    _require(rc.get("schema") == "nexus.osr.rc-test-candidate.v1", "rc_schema_invalid")
    _require(rc.get("decision") == "RC0_TEST_DEPLOYABLE", "rc_decision_invalid")
    candidate = rc.get("candidate")
    _require(isinstance(candidate, dict), "rc_candidate_invalid")
    _require(candidate.get("source_sha") == source, "rc_source_mismatch")
    _require(candidate.get("frontend_build_sha") == frontend, "rc_frontend_mismatch")
    _require(candidate.get("image_id") == local_image, "rc_image_mismatch")
    _require(candidate.get("migration_revision") == migration, "rc_migration_mismatch")
    checks = rc.get("checks")
    _require(isinstance(checks, dict) and checks, "rc_checks_invalid")
    _require(all(value == "pass" for value in checks.values()), "rc_checks_not_pass")
    safety = rc.get("safety")
    _require(isinstance(safety, dict), "rc_safety_invalid")
    expected_safety = {
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
    for key, expected in expected_safety.items():
        _require(safety.get(key) == expected, f"rc_safety_invalid:{key}")

    _require(
        assurance.get("schema_version") == "nexus_release_image_assurance_v1",
        "assurance_schema_invalid",
    )
    _require(assurance.get("status") == "pass", "assurance_status_invalid")
    _require(assurance.get("source_sha") == source, "assurance_source_mismatch")
    _require(assurance.get("image_id") == local_image, "assurance_image_mismatch")
    _require(int(assurance.get("critical_count") or 0) == 0, "assurance_critical_findings")
    _require(int(assurance.get("high_count") or 0) == 0, "assurance_high_findings")
    _require(int(assurance.get("unresolved_license_count") or 0) == 0, "assurance_license_findings")
    _require(assurance.get("image_pushed") is False, "assurance_pre_publish_state_invalid")
    _require(assurance.get("deployment_performed") is False, "assurance_deployment_state_invalid")

    _require(
        binding.get("schema_version") == "nexus_release_image_compliance_binding_v1",
        "binding_schema_invalid",
    )
    _require(binding.get("status") == "pass", "binding_status_invalid")
    _require(binding.get("source_sha") == source, "binding_source_mismatch")
    _require(binding.get("image_id") == local_image, "binding_image_mismatch")
    _require(binding.get("image_pushed") is False, "binding_pre_publish_state_invalid")
    _require(binding.get("deployment_performed") is False, "binding_deployment_state_invalid")

    _require(
        recovery.get("schema_version") == "nexus_postgres_recovery_qualification_v1",
        "recovery_schema_invalid",
    )
    _require(recovery.get("status") == "pass", "recovery_status_invalid")
    _require(recovery.get("source_sha") == source, "recovery_source_mismatch")
    _require(recovery.get("alembic_head") == migration, "recovery_migration_mismatch")
    _require(recovery.get("reasons") == [], "recovery_reasons_present")
    _require(recovery.get("foreign_key_definitions_match") is True, "recovery_fk_mismatch")
    _require(recovery.get("foreign_keys_validated") is True, "recovery_fk_not_validated")
    _require(recovery.get("synthetic_marker_restored") is True, "recovery_marker_missing")
    _require(recovery.get("production_data_used") is False, "recovery_production_data_invalid")
    _require(
        recovery.get("production_mutation_performed") is False,
        "recovery_production_mutation_invalid",
    )

    _require(
        receipt.get("schema") == "nexus.osr.registry-publish-receipt.v1",
        "publish_receipt_schema_invalid",
    )
    _require(receipt.get("status") == "pass", "publish_receipt_status_invalid")
    _require(receipt.get("source_sha") == source, "publish_receipt_source_mismatch")
    _require(receipt.get("registry_image") == image_name, "publish_receipt_image_mismatch")
    _require(receipt.get("registry_digest") == digest, "publish_receipt_digest_mismatch")
    _require(receipt.get("local_image_id") == local_image, "publish_receipt_local_image_mismatch")
    _require(receipt.get("pulled_image_id") == pulled_image, "publish_receipt_pull_image_mismatch")
    _require(receipt.get("image_pushed") is True, "publish_receipt_not_pushed")
    _require(receipt.get("deployment_performed") is False, "publish_receipt_deployment_invalid")

    registry_reference = f"{image_name}@{digest}"
    _require(receipt.get("registry_reference") == registry_reference, "publish_receipt_reference_mismatch")

    inputs = {
        "rc_candidate_manifest": {
            "path": rc_manifest_path.name,
            "sha256": _digest(rc_manifest_path),
        },
        "release_image_manifest": {
            "path": release_image_manifest_path.name,
            "sha256": _digest(release_image_manifest_path),
        },
        "release_image_compliance_binding": {
            "path": compliance_binding_path.name,
            "sha256": _digest(compliance_binding_path),
        },
        "recovery_evidence": {
            "path": recovery_evidence_path.name,
            "sha256": _digest(recovery_evidence_path),
        },
        "registry_publish_receipt": {
            "path": publish_receipt_path.name,
            "sha256": _digest(publish_receipt_path),
        },
    }

    payload = {
        "schema": "nexus.osr.controlled-candidate-manifest.v1",
        "status": "pass",
        "decision": "CONTROLLED_SERVER_CANDIDATE_PUBLISHED",
        "release_class": "controlled_server_deployment",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "candidate": {
            "source_sha": source,
            "frontend_build_sha": frontend,
            "migration_revision": migration,
            "local_image_id": local_image,
            "registry_pull_image_id": pulled_image,
            "registry_image": image_name,
            "registry_digest": digest,
            "registry_reference": registry_reference,
            "config_profile": candidate.get("config_profile"),
            "config_digest": candidate.get("config_digest"),
            "postgres_image_digest": candidate.get("postgres_image_digest"),
            "nginx_image_digest": candidate.get("nginx_image_digest"),
        },
        "attestation": {
            "id": str(attestation_id).strip(),
            "url": attestation_url_value,
            "registry_provenance_pushed": True,
        },
        "evidence": inputs,
        "safety": {
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
        },
    }
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if len(encoded.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise ManifestError("output_too_large")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(encoded, encoding="utf-8")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--registry-image", required=True)
    parser.add_argument("--registry-digest", required=True)
    parser.add_argument("--local-image-id", required=True)
    parser.add_argument("--pulled-image-id", required=True)
    parser.add_argument("--migration-head", required=True)
    parser.add_argument("--frontend-sha", required=True)
    parser.add_argument("--attestation-id", required=True)
    parser.add_argument("--attestation-url", required=True)
    parser.add_argument("--rc-manifest", type=Path, required=True)
    parser.add_argument("--release-image-manifest", type=Path, required=True)
    parser.add_argument("--compliance-binding", type=Path, required=True)
    parser.add_argument("--recovery-evidence", type=Path, required=True)
    parser.add_argument("--publish-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        return build_manifest(
            source_sha=args.source_sha,
            registry_image=args.registry_image,
            registry_digest=args.registry_digest,
            local_image_id=args.local_image_id,
            pulled_image_id=args.pulled_image_id,
            migration_head=args.migration_head,
            frontend_sha=args.frontend_sha,
            attestation_id=args.attestation_id,
            attestation_url=args.attestation_url,
            rc_manifest_path=args.rc_manifest,
            release_image_manifest_path=args.release_image_manifest,
            compliance_binding_path=args.compliance_binding,
            recovery_evidence_path=args.recovery_evidence,
            publish_receipt_path=args.publish_receipt,
            output_path=args.output,
        )
    except (ManifestError, OSError, ValueError) as exc:
        print(f"controlled_candidate_manifest_error:{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
