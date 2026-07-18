#!/usr/bin/env python3
"""Validate one immutable, sanitized exact-Head acceptance evidence packet.

The repository verifier remains the only entrypoint. This module is a bounded
qualification subroutine: it does not execute production actions, invent
results, or accept unbound evidence. Every required artifact is referenced by a
single manifest that pins source/tree identity and SHA-256 content.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
IMMUTABLE_IMAGE = re.compile(r"^[a-z0-9._/-]+(?:\:[a-z0-9._-]+)?@sha256:[0-9a-f]{64}$")
MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
PASS_STATUSES = {"pass", "ok", "evidence_complete", "approved"}

REQUIRED_ARTIFACTS: dict[str, tuple[str, str]] = {
    "supply_chain": ("supply-chain.json", "nexus.supply-chain-qualification.v1"),
    "signature_verification": ("signature-verification.json", "nexus.signature-verification.v1"),
    "migration_rehearsal": ("migration-rehearsal.json", "nexus.migration-rehearsal.v1"),
    "postgres_qualification": ("postgres-qualification.json", "nexus.postgres-qualification.v1"),
    "database_capacity": ("database-capacity.json", "nexus.database-capacity-snapshot.v1"),
    "representative_workload": ("representative-workload.json", "nexus.representative-workload.v1"),
    "worker_fault_injection": ("worker-fault-injection.json", "nexus.worker-fault-injection.v1"),
    "upload_backup": ("upload-backup.json", "nexus.local-storage-backup.v1"),
    "recovery_rehearsal": ("recovery-rehearsal.json", "nexus.recovery-rehearsal.v1"),
    "controlled_deployment": ("controlled-deployment.json", "nexus.controlled-deployment-acceptance.v1"),
    "rollback_rehearsal": ("rollback-rehearsal.json", "nexus.rollback-rehearsal.v1"),
    "queue_baseline": ("queue-baseline.json", "nexus.queue-baseline.v1"),
    "realtime_baseline": ("realtime-baseline.json", "nexus.realtime-baseline.v1"),
    "storage_baseline": ("storage-baseline.json", "nexus.storage-baseline.v1"),
    "infrastructure_decisions": ("infrastructure-decisions.json", "nexus.infrastructure-decision.v1"),
    "independent_review": ("independent-review.json", "nexus.independent-review.v1"),
    "repository_protection": ("repository-protection.json", "nexus.repository-protection.v1"),
}


def _inside_repository(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return False
    return True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path, *, label: str, findings: list[str]) -> dict[str, Any] | None:
    if path.is_symlink():
        findings.append(f"artifact_symlink_forbidden:{label}")
        return None
    if not path.is_file():
        findings.append(f"artifact_missing:{label}")
        return None
    size = path.stat().st_size
    if size <= 0:
        findings.append(f"artifact_empty:{label}")
        return None
    if size > MAX_ARTIFACT_BYTES:
        findings.append(f"artifact_too_large:{label}")
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        findings.append(f"artifact_invalid_json:{label}")
        return None
    if not isinstance(payload, dict):
        findings.append(f"artifact_root_invalid:{label}")
        return None
    return payload


def _number(payload: dict[str, Any], key: str, findings: list[str], label: str) -> float | None:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        findings.append(f"artifact_number_missing:{label}:{key}")
        return None
    return float(value)


def _status_pass(payload: dict[str, Any], label: str, findings: list[str]) -> None:
    if payload.get("status") not in PASS_STATUSES:
        findings.append(f"artifact_status_not_pass:{label}:{payload.get('status')}")


def _common_sanitization(payload: dict[str, Any], label: str, findings: list[str]) -> None:
    if payload.get("sanitized") is not True:
        findings.append(f"artifact_not_sanitized:{label}")
    if payload.get("contains_customer_data") is not False:
        findings.append(f"artifact_customer_data_boundary_missing:{label}")
    if payload.get("contains_secrets") is not False:
        findings.append(f"artifact_secret_boundary_missing:{label}")


def _check_supply_chain(payload: dict[str, Any], findings: list[str]) -> None:
    _status_pass(payload, "supply_chain", findings)
    evidence = payload.get("evidence")
    if not isinstance(evidence, dict):
        findings.append("supply_chain_evidence_missing")
        return
    if evidence.get("candidate_tree_mutated") is not False:
        findings.append("supply_chain_candidate_tree_mutated")
    for name in ("sbom", "provenance", "signature_bundle"):
        row = evidence.get(name)
        if not isinstance(row, dict) or not SHA256.fullmatch(str(row.get("sha256") or "")):
            findings.append(f"supply_chain_artifact_hash_missing:{name}")


def _check_signature(payload: dict[str, Any], findings: list[str]) -> None:
    _status_pass(payload, "signature_verification", findings)
    _common_sanitization(payload, "signature_verification", findings)
    if payload.get("verified") is not True:
        findings.append("signature_not_verified")
    if not IMMUTABLE_IMAGE.fullmatch(str(payload.get("image") or "")):
        findings.append("signature_image_not_immutable")
    if not str(payload.get("verification_identity") or "").strip():
        findings.append("signature_verification_identity_missing")


def _check_migration(payload: dict[str, Any], findings: list[str]) -> None:
    _status_pass(payload, "migration_rehearsal", findings)
    _common_sanitization(payload, "migration_rehearsal", findings)
    if payload.get("database_disposable") is not True:
        findings.append("migration_database_not_disposable")
    for key in ("upgrade_passed", "downgrade_passed", "reupgrade_passed"):
        if payload.get(key) is not True:
            findings.append(f"migration_step_failed:{key}")
    if not str(payload.get("final_revision") or "").strip():
        findings.append("migration_final_revision_missing")


def _check_postgres(payload: dict[str, Any], findings: list[str]) -> None:
    _status_pass(payload, "postgres_qualification", findings)
    _common_sanitization(payload, "postgres_qualification", findings)
    if payload.get("database_disposable") is not True:
        findings.append("postgres_database_not_disposable")
    for key in (
        "tests_passed",
        "cross_scope_existence_safe",
        "lists_minimized",
        "sensitive_access_audited",
        "lease_fencing_passed",
    ):
        if payload.get(key) is not True:
            findings.append(f"postgres_qualification_failed:{key}")


def _check_database_capacity(payload: dict[str, Any], findings: list[str]) -> None:
    _status_pass(payload, "database_capacity", findings)
    if payload.get("sanitized") is not True:
        findings.append("database_capacity_not_sanitized")
    budget = payload.get("budget")
    runtime = payload.get("runtime")
    if not isinstance(budget, dict) or budget.get("within_budget") is not True:
        findings.append("database_capacity_budget_failed")
    if not isinstance(runtime, dict):
        findings.append("database_capacity_runtime_missing")
    elif runtime.get("query_text_included") is not False:
        findings.append("database_capacity_query_text_present")


def _check_workload(payload: dict[str, Any], findings: list[str]) -> None:
    _status_pass(payload, "representative_workload", findings)
    _common_sanitization(payload, "representative_workload", findings)
    sample_count = _number(payload, "sample_count", findings, "representative_workload")
    if sample_count is not None and sample_count <= 0:
        findings.append("representative_workload_empty")
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict):
        findings.append("representative_workload_metrics_missing")
        return
    for key in (
        "latency_p50_ms",
        "latency_p95_ms",
        "latency_p99_ms",
        "throughput_per_second",
        "error_rate_percent",
        "pool_checkout_wait_p95_ms",
        "worker_busy_ratio_percent",
        "cpu_headroom_percent",
    ):
        _number(metrics, key, findings, "representative_workload.metrics")


def _check_worker_fault(payload: dict[str, Any], findings: list[str]) -> None:
    _status_pass(payload, "worker_fault_injection", findings)
    _common_sanitization(payload, "worker_fault_injection", findings)
    required = {
        "kill_after_claim",
        "kill_during_external_wait",
        "database_disconnect_before_commit",
        "lease_transfer",
        "stale_worker_resume",
        "ambiguous_external_result",
        "full_worker_restart",
    }
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, dict):
        findings.append("worker_fault_scenarios_missing")
    else:
        for name in sorted(required):
            row = scenarios.get(name)
            if not isinstance(row, dict) or row.get("status") != "pass":
                findings.append(f"worker_fault_scenario_failed:{name}")
    for key in (
        "no_stuck_processing",
        "no_stale_completion",
        "no_duplicate_durable_action",
        "no_duplicate_external_action",
        "bounded_retry_or_dead_state",
    ):
        if payload.get(key) is not True:
            findings.append(f"worker_fault_invariant_failed:{key}")


def _check_upload_backup(payload: dict[str, Any], findings: list[str]) -> None:
    if payload.get("source_matches_backup") is not True:
        findings.append("upload_backup_manifest_mismatch")
    if payload.get("contains_file_names") is not False:
        findings.append("upload_backup_contains_file_names")
    if payload.get("contains_file_content") is not False:
        findings.append("upload_backup_contains_file_content")
    if not SHA256.fullmatch(str(payload.get("manifest_sha256") or "")):
        findings.append("upload_backup_manifest_hash_missing")


def _check_recovery(payload: dict[str, Any], findings: list[str]) -> None:
    _status_pass(payload, "recovery_rehearsal", findings)
    _common_sanitization(payload, "recovery_rehearsal", findings)
    for key in (
        "disposable_environment",
        "database_restored",
        "uploads_restored",
        "referential_checks_passed",
        "attachment_reads_passed",
    ):
        if payload.get(key) is not True:
            findings.append(f"recovery_invariant_failed:{key}")
    rpo = _number(payload, "rpo_seconds", findings, "recovery_rehearsal")
    rto = _number(payload, "rto_seconds", findings, "recovery_rehearsal")
    if rpo is not None and rpo < 0:
        findings.append("recovery_rpo_invalid")
    if rto is not None and rto <= 0:
        findings.append("recovery_rto_invalid")


def _check_controlled_deployment(payload: dict[str, Any], findings: list[str]) -> None:
    _status_pass(payload, "controlled_deployment", findings)
    _common_sanitization(payload, "controlled_deployment", findings)
    for key in (
        "healthz_passed",
        "readyz_passed",
        "release_identity_matches",
        "queue_health_ready",
        "database_pool_ready",
        "storage_backup_fresh",
        "service_database_identities_isolated",
        "external_writes_fail_closed",
    ):
        if payload.get(key) is not True:
            findings.append(f"controlled_deployment_failed:{key}")
    if not IMMUTABLE_IMAGE.fullmatch(str(payload.get("image") or "")):
        findings.append("controlled_deployment_image_not_immutable")


def _check_rollback(payload: dict[str, Any], findings: list[str]) -> None:
    _status_pass(payload, "rollback_rehearsal", findings)
    _common_sanitization(payload, "rollback_rehearsal", findings)
    for key in (
        "previous_release_identity_verified",
        "rollback_passed",
        "healthz_passed",
        "readyz_passed",
    ):
        if payload.get(key) is not True:
            findings.append(f"rollback_rehearsal_failed:{key}")
    duration = _number(payload, "duration_seconds", findings, "rollback_rehearsal")
    if duration is not None and duration <= 0:
        findings.append("rollback_duration_invalid")


def _check_baseline(payload: dict[str, Any], label: str, findings: list[str]) -> None:
    _status_pass(payload, label, findings)
    _common_sanitization(payload, label, findings)
    sample_count = _number(payload, "sample_count", findings, label)
    if sample_count is not None and sample_count <= 0:
        findings.append(f"baseline_empty:{label}")


def _check_infrastructure(payload: dict[str, Any], findings: list[str]) -> None:
    if payload.get("status") != "evidence_complete":
        findings.append("infrastructure_evidence_incomplete")
    if payload.get("automatic_change_authorized") is not False:
        findings.append("infrastructure_automatic_change_authorized")
    decisions = payload.get("decisions")
    if not isinstance(decisions, dict):
        findings.append("infrastructure_decisions_missing")
        return
    for name in ("pgbouncer", "redis", "object_storage", "additional_workers"):
        row = decisions.get(name)
        if not isinstance(row, dict):
            findings.append(f"infrastructure_decision_missing:{name}")
            continue
        if row.get("decision") not in {"NO_CHANGE", "CONSIDER_ADR"}:
            findings.append(f"infrastructure_decision_unresolved:{name}:{row.get('decision')}")


def _check_review(payload: dict[str, Any], findings: list[str]) -> None:
    _status_pass(payload, "independent_review", findings)
    _common_sanitization(payload, "independent_review", findings)
    if payload.get("independent") is not True:
        findings.append("review_not_independent")
    if payload.get("decision") != "approved":
        findings.append("review_not_approved")
    if not str(payload.get("reviewer_identity") or "").strip():
        findings.append("reviewer_identity_missing")
    if payload.get("unresolved_findings") != []:
        findings.append("review_has_unresolved_findings")
    for key in (
        "no_second_ui_confirmed",
        "no_second_transport_confirmed",
        "no_second_permission_authority_confirmed",
        "no_second_provider_authority_confirmed",
        "no_second_worker_authority_confirmed",
        "no_second_release_authority_confirmed",
    ):
        if payload.get(key) is not True:
            findings.append(f"review_confirmation_missing:{key}")


def _check_protection(payload: dict[str, Any], findings: list[str]) -> None:
    _status_pass(payload, "repository_protection", findings)
    _common_sanitization(payload, "repository_protection", findings)
    for key in (
        "direct_main_writes_restricted",
        "independent_review_required",
        "stale_head_evidence_invalidated",
        "expected_head_merge_enforced",
        "administrator_bypass_restricted",
    ):
        if payload.get(key) is not True:
            findings.append(f"repository_protection_missing:{key}")


CHECKS: dict[str, Callable[[dict[str, Any], list[str]], None]] = {
    "supply_chain": _check_supply_chain,
    "signature_verification": _check_signature,
    "migration_rehearsal": _check_migration,
    "postgres_qualification": _check_postgres,
    "database_capacity": _check_database_capacity,
    "representative_workload": _check_workload,
    "worker_fault_injection": _check_worker_fault,
    "upload_backup": _check_upload_backup,
    "recovery_rehearsal": _check_recovery,
    "controlled_deployment": _check_controlled_deployment,
    "rollback_rehearsal": _check_rollback,
    "queue_baseline": lambda payload, findings: _check_baseline(payload, "queue_baseline", findings),
    "realtime_baseline": lambda payload, findings: _check_baseline(payload, "realtime_baseline", findings),
    "storage_baseline": lambda payload, findings: _check_baseline(payload, "storage_baseline", findings),
    "infrastructure_decisions": _check_infrastructure,
    "independent_review": _check_review,
    "repository_protection": _check_protection,
}


def _manifest_status(label: str, payload: dict[str, Any]) -> str:
    if label == "upload_backup":
        return "pass" if payload.get("source_matches_backup") is True else "fail"
    return str(payload.get("status") or "")


def assemble_acceptance_manifest(
    evidence_dir: Path,
    *,
    source_sha: str,
    tree_sha: str,
    manifest_name: str = "acceptance-manifest.json",
) -> dict[str, Any]:
    directory = evidence_dir.expanduser().resolve()
    if _inside_repository(directory) or not directory.is_dir() or directory.is_symlink():
        raise ValueError("acceptance_evidence_directory_invalid")
    if not SHA40.fullmatch(source_sha) or not SHA40.fullmatch(tree_sha):
        raise ValueError("acceptance_identity_invalid")
    findings: list[str] = []
    artifacts: dict[str, dict[str, str]] = {}
    for label, (name, schema) in REQUIRED_ARTIFACTS.items():
        path = directory / name
        payload = _load_json(path, label=label, findings=findings)
        if payload is None:
            continue
        status = _manifest_status(label, payload)
        if payload.get("schema") != schema:
            findings.append(f"acceptance_artifact_schema_mismatch:{label}")
        if status not in PASS_STATUSES:
            findings.append(f"acceptance_artifact_status_not_pass:{label}:{status}")
        artifacts[label] = {
            "path": name,
            "sha256": _sha256(path),
            "schema": schema,
            "status": status,
        }
    if findings:
        raise ValueError("acceptance_manifest_assembly_failed:" + "|".join(findings[:50]))
    manifest = {
        "schema": "nexus.exact-head-acceptance-manifest.v1",
        "source_sha": source_sha,
        "tree_sha": tree_sha,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sanitized": True,
        "contains_customer_data": False,
        "contains_secrets": False,
        "artifacts": artifacts,
    }
    target = directory / manifest_name
    if target.is_symlink():
        raise ValueError("acceptance_manifest_symlink_forbidden")
    temporary = directory / f".{manifest_name}.tmp"
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(target)
    return manifest


def qualify_acceptance_packet(
    evidence_dir: Path,
    *,
    expected_source_sha: str,
    expected_tree_sha: str,
    manifest_name: str = "acceptance-manifest.json",
) -> dict[str, Any]:
    findings: list[str] = []
    directory = evidence_dir.expanduser().resolve()
    if _inside_repository(directory):
        findings.append("acceptance_evidence_inside_candidate_tree")
    if not directory.is_dir() or directory.is_symlink():
        findings.append("acceptance_evidence_directory_invalid")
        return {
            "schema": "nexus.exact-head-acceptance-qualification.v1",
            "status": "fail",
            "findings": findings,
        }
    if not SHA40.fullmatch(expected_source_sha):
        findings.append("expected_source_sha_invalid")
    if not SHA40.fullmatch(expected_tree_sha):
        findings.append("expected_tree_sha_invalid")

    manifest_path = directory / manifest_name
    manifest = _load_json(manifest_path, label="acceptance_manifest", findings=findings)
    if manifest is None:
        return {
            "schema": "nexus.exact-head-acceptance-qualification.v1",
            "status": "fail",
            "findings": findings,
        }
    if manifest.get("schema") != "nexus.exact-head-acceptance-manifest.v1":
        findings.append("acceptance_manifest_schema_invalid")
    if manifest.get("source_sha") != expected_source_sha:
        findings.append("acceptance_manifest_source_sha_mismatch")
    if manifest.get("tree_sha") != expected_tree_sha:
        findings.append("acceptance_manifest_tree_sha_mismatch")
    try:
        timestamp = datetime.fromisoformat(str(manifest.get("generated_at") or "").replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise ValueError("timezone required")
    except ValueError:
        findings.append("acceptance_manifest_timestamp_invalid")
    if manifest.get("sanitized") is not True:
        findings.append("acceptance_manifest_not_sanitized")
    if manifest.get("contains_customer_data") is not False:
        findings.append("acceptance_manifest_customer_data_boundary_missing")
    if manifest.get("contains_secrets") is not False:
        findings.append("acceptance_manifest_secret_boundary_missing")

    artifact_rows = manifest.get("artifacts")
    if not isinstance(artifact_rows, dict):
        findings.append("acceptance_manifest_artifacts_invalid")
        artifact_rows = {}

    verified: dict[str, dict[str, Any]] = {}
    for label, (default_name, expected_schema) in REQUIRED_ARTIFACTS.items():
        row = artifact_rows.get(label)
        if not isinstance(row, dict):
            findings.append(f"acceptance_artifact_reference_missing:{label}")
            continue
        relative = str(row.get("path") or default_name)
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            findings.append(f"acceptance_artifact_path_invalid:{label}")
            continue
        unresolved = directory / candidate
        if unresolved.is_symlink():
            findings.append(f"artifact_symlink_forbidden:{label}")
            continue
        path = unresolved.resolve()
        try:
            path.relative_to(directory)
        except ValueError:
            findings.append(f"acceptance_artifact_path_escape:{label}")
            continue
        expected_hash = str(row.get("sha256") or "")
        if not SHA256.fullmatch(expected_hash):
            findings.append(f"acceptance_artifact_hash_invalid:{label}")
        payload = _load_json(path, label=label, findings=findings)
        if payload is None:
            continue
        actual_hash = _sha256(path)
        if expected_hash != actual_hash:
            findings.append(f"acceptance_artifact_hash_mismatch:{label}")
        if row.get("schema") != expected_schema or payload.get("schema") != expected_schema:
            findings.append(f"acceptance_artifact_schema_mismatch:{label}")
        if row.get("status") not in PASS_STATUSES:
            findings.append(f"acceptance_artifact_manifest_status_invalid:{label}")
        for key, expected in (("source_sha", expected_source_sha), ("tree_sha", expected_tree_sha)):
            value = payload.get(key)
            if value is not None and value != expected:
                findings.append(f"acceptance_artifact_identity_mismatch:{label}:{key}")
        CHECKS[label](payload, findings)
        verified[label] = {
            "path": relative,
            "sha256": actual_hash,
            "schema": payload.get("schema"),
        }

    undeclared = sorted(set(artifact_rows) - set(REQUIRED_ARTIFACTS))
    if undeclared:
        findings.append(f"acceptance_manifest_undeclared_artifacts:{','.join(undeclared)}")

    return {
        "schema": "nexus.exact-head-acceptance-qualification.v1",
        "status": "pass" if not findings else "fail",
        "source_sha": expected_source_sha,
        "tree_sha": expected_tree_sha,
        "manifest_sha256": _sha256(manifest_path),
        "artifact_count": len(verified),
        "required_artifact_count": len(REQUIRED_ARTIFACTS),
        "findings": findings,
        "verified_artifacts": verified,
        "production_authorized": False,
        "provider_enablement_authorized": False,
        "outbound_enablement_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--tree-sha", required=True)
    parser.add_argument("--manifest-name", default="acceptance-manifest.json")
    parser.add_argument("--assemble-manifest", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.assemble_manifest:
        assemble_acceptance_manifest(
            args.evidence_dir,
            source_sha=args.source_sha,
            tree_sha=args.tree_sha,
            manifest_name=args.manifest_name,
        )
    payload = qualify_acceptance_packet(
        args.evidence_dir,
        expected_source_sha=args.source_sha,
        expected_tree_sha=args.tree_sha,
        manifest_name=args.manifest_name,
    )
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        output = args.output.expanduser().resolve()
        if _inside_repository(output):
            raise SystemExit("acceptance qualification output must remain outside candidate tree")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
