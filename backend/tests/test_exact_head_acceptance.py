from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "nexus_exact_head_acceptance",
    ROOT / "scripts/qualification/exact_head_acceptance.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_required_acceptance_domains_are_explicit() -> None:
    assert set(MODULE.REQUIRED_ARTIFACTS) == {
        "supply_chain",
        "signature_verification",
        "migration_rehearsal",
        "postgres_qualification",
        "database_capacity",
        "representative_workload",
        "worker_fault_injection",
        "upload_backup",
        "recovery_rehearsal",
        "controlled_deployment",
        "rollback_rehearsal",
        "queue_baseline",
        "realtime_baseline",
        "storage_baseline",
        "infrastructure_decisions",
        "independent_review",
        "repository_protection",
    }


def test_missing_manifest_fails_closed(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    result = MODULE.qualify_acceptance_packet(
        evidence,
        expected_source_sha="a" * 40,
        expected_tree_sha="b" * 40,
    )
    assert result["status"] == "fail"
    assert "artifact_missing:acceptance_manifest" in result["findings"]


def test_signature_requires_verified_immutable_image() -> None:
    findings: list[str] = []
    MODULE._check_signature(
        {
            "schema": "nexus.signature-verification.v1",
            "status": "pass",
            "sanitized": True,
            "contains_customer_data": False,
            "contains_secrets": False,
            "verified": False,
            "image": "mutable:latest",
            "verification_identity": "",
        },
        findings,
    )
    assert "signature_not_verified" in findings
    assert "signature_image_not_immutable" in findings
    assert "signature_verification_identity_missing" in findings


def test_review_cannot_pass_with_open_findings() -> None:
    findings: list[str] = []
    MODULE._check_review(
        {
            "schema": "nexus.independent-review.v1",
            "status": "pass",
            "sanitized": True,
            "contains_customer_data": False,
            "contains_secrets": False,
            "independent": True,
            "decision": "approved",
            "reviewer_identity": "reviewer",
            "unresolved_findings": ["residual"],
            "no_second_ui_confirmed": True,
            "no_second_transport_confirmed": True,
            "no_second_permission_authority_confirmed": True,
            "no_second_provider_authority_confirmed": True,
            "no_second_worker_authority_confirmed": True,
            "no_second_release_authority_confirmed": True,
        },
        findings,
    )
    assert "review_has_unresolved_findings" in findings


def test_worker_fault_evidence_requires_every_scenario() -> None:
    findings: list[str] = []
    MODULE._check_worker_fault(
        {
            "schema": "nexus.worker-fault-injection.v1",
            "status": "pass",
            "sanitized": True,
            "contains_customer_data": False,
            "contains_secrets": False,
            "scenarios": {},
            "no_stuck_processing": True,
            "no_stale_completion": True,
            "no_duplicate_durable_action": True,
            "no_duplicate_external_action": True,
            "bounded_retry_or_dead_state": True,
        },
        findings,
    )
    assert any(item.startswith("worker_fault_scenario_failed:") for item in findings)
