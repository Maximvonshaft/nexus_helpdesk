from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.services.nexus_osr.runtime_decision_contract import (
    EvidenceSource,
    RuntimeDecision,
    RuntimeToolAction,
    evaluate_runtime_decision,
)

from .schema import RESULT_SCHEMA_VERSION, GovernedDataset

_MAX_FAILURE_DETAILS = 25
_DEFAULT_MAX_ARTIFACT_BYTES = 64 * 1024
_PERMISSION_RANK = {
    "public": 0,
    "customer": 1,
    "operator": 2,
    "admin": 3,
    "system": 4,
}


def evaluate_dataset(dataset: GovernedDataset) -> dict[str, Any]:
    payload = dataset.payload
    digest = _dataset_digest(payload)
    case_results = [_evaluate_case(case) for case in sorted(dataset.cases, key=lambda item: item["id"])]
    coverage = _coverage(payload["coverage_requirements"], dataset.cases)
    failed_case_ids = [item["id"] for item in case_results if not item["ok"]]
    coverage_failed = any(coverage["gaps"][dimension] for dimension in coverage["gaps"])
    passed = len(case_results) - len(failed_case_ids)

    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "dataset": {
            "id": payload["dataset_id"],
            "version": payload["dataset_version"],
            "schema_version": payload["schema_version"],
            "digest_sha256": digest,
            "governance_status": payload["governance"]["status"],
            "owner_id": payload["governance"]["owner_id"],
            "approver_id": payload["governance"]["approver_id"],
            "review_date": payload["governance"]["review_date"],
            "valid_until": payload["governance"]["valid_until"],
        },
        "run": {
            "run_id": f"osr-eval-{digest[:12]}",
            "deterministic": True,
            "read_only": True,
            "case_count": len(case_results),
            "passed": passed,
            "failed": len(failed_case_ids),
            "coverage_failed": coverage_failed,
            "ok": not failed_case_ids and not coverage_failed,
        },
        "safety": {
            "source_classification": payload["governance"]["source_classification"],
            "production_payloads_collected": False,
            "customer_visible_behavior_changes": False,
            "messages_sent": 0,
            "tools_executed": 0,
            "production_mutations": 0,
            "raw_case_payloads_emitted": False,
        },
        "coverage": coverage,
        "cases": case_results,
    }


def write_artifacts(
    report: dict[str, Any],
    output_dir: str | Path,
    *,
    max_artifact_bytes: int = _DEFAULT_MAX_ARTIFACT_BYTES,
) -> dict[str, Any]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)

    summary = _bounded_summary(report)
    failure_cases = [case for case in report["cases"] if not case["ok"]]
    failures = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "dataset_id": report["dataset"]["id"],
        "dataset_version": report["dataset"]["version"],
        "failed_count": len(failure_cases),
        "included_count": min(len(failure_cases), _MAX_FAILURE_DETAILS),
        "truncated": len(failure_cases) > _MAX_FAILURE_DETAILS,
        "failures": failure_cases[:_MAX_FAILURE_DETAILS],
    }
    coverage = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "dataset_id": report["dataset"]["id"],
        "dataset_version": report["dataset"]["version"],
        "coverage": report["coverage"],
    }

    artifacts = {
        "summary.json": summary,
        "failures.json": failures,
        "coverage.json": coverage,
    }
    manifest_entries: list[dict[str, Any]] = []
    for name, payload in artifacts.items():
        raw = _json_bytes(payload)
        if len(raw) > max_artifact_bytes:
            raise ValueError(f"artifact_too_large:{name}:{len(raw)}>{max_artifact_bytes}")
        path = target / name
        path.write_bytes(raw)
        manifest_entries.append(
            {
                "name": name,
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )

    manifest = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "dataset_id": report["dataset"]["id"],
        "dataset_version": report["dataset"]["version"],
        "max_artifact_bytes": max_artifact_bytes,
        "artifacts": sorted(manifest_entries, key=lambda item: item["name"]),
        "bounded": True,
        "redacted": True,
    }
    manifest_raw = _json_bytes(manifest)
    if len(manifest_raw) > max_artifact_bytes:
        raise ValueError("artifact_too_large:manifest.json")
    (target / "manifest.json").write_bytes(manifest_raw)
    return manifest


def _evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    boundary_violations: list[str] = []
    if case["tenant_key"] != case["expected_tenant_key"]:
        boundary_violations.append("tenant_scope_mismatch")
    if _PERMISSION_RANK[case["actor_permission"]] < _PERMISSION_RANK[case["required_permission"]]:
        boundary_violations.append("permission_denied")
    boundary_violations.extend(
        f"unsafe_output_marker:{marker}" for marker in case["synthetic_unsafe_markers"]
    )

    decision_payload = case["decision"]
    decision = RuntimeDecision(
        business_reply_type=decision_payload["business_reply_type"],
        next_action=decision_payload["next_action"],
        customer_reply=decision_payload["customer_reply"],
        language=decision_payload["language"],
        risk_level=decision_payload["risk_level"],
        evidence_sources=[_build_evidence(item) for item in decision_payload["evidence_sources"]],
        tool_actions=[_build_tool_action(item) for item in decision_payload["tool_actions"]],
        handoff_required=decision_payload["handoff_required"],
        ticket_required=decision_payload["ticket_required"],
        routing_required=decision_payload["routing_required"],
    )
    runtime_evaluation = evaluate_runtime_decision(decision)
    violation_codes = sorted(
        set(boundary_violations + [violation.code for violation in runtime_evaluation.violations])
    )
    warning_codes = sorted(set(runtime_evaluation.warnings))
    actual_allowed = not violation_codes
    boundary = "allow" if not boundary_violations else "deny"
    customer_visible = (
        bool(decision.customer_reply) and actual_allowed and str(decision.next_action) != "block"
    )

    expected = case["expected"]
    mismatches: list[str] = []
    if actual_allowed != expected["allowed"]:
        mismatches.append("allowed")
    if violation_codes != expected["violation_codes"]:
        mismatches.append("violation_codes")
    if customer_visible != expected["customer_visible"]:
        mismatches.append("customer_visible")
    if boundary != expected["boundary"]:
        mismatches.append("boundary")

    return {
        "id": case["id"],
        "category": case["category"],
        "country_code": case["country_code"],
        "channel": case["channel"],
        "language": case["language"],
        "risk_level": case["risk_level"],
        "actor_permission": case["actor_permission"],
        "actual": {
            "allowed": actual_allowed,
            "violation_codes": violation_codes,
            "warning_codes": warning_codes,
            "customer_visible": customer_visible,
            "boundary": boundary,
        },
        "expected": {
            "allowed": expected["allowed"],
            "violation_codes": expected["violation_codes"],
            "customer_visible": expected["customer_visible"],
            "boundary": expected["boundary"],
        },
        "mismatches": mismatches,
        "ok": not mismatches,
    }


def _build_evidence(payload: dict[str, Any]) -> EvidenceSource:
    return EvidenceSource(
        evidence_type=payload["evidence_type"],
        source_id=payload["source_id"],
        label=payload["label"],
        summary=dict(payload.get("summary") or {}),
        confidence=float(payload.get("confidence", 1.0)),
        customer_visible=bool(payload.get("customer_visible", False)),
        verified=bool(payload.get("verified", False)),
        current_status=bool(payload.get("current_status", False)),
        created_at=payload.get("created_at"),
    )


def _build_tool_action(payload: dict[str, Any]) -> RuntimeToolAction:
    return RuntimeToolAction(
        tool_name=payload["tool_name"],
        arguments={},
        requires_confirmation=bool(payload.get("requires_confirmation", False)),
        executed=bool(payload.get("executed", False)),
        result_source_id=payload.get("result_source_id"),
    )


def _coverage(requirements: dict[str, list[str]], cases: list[dict[str, Any]]) -> dict[str, Any]:
    actual = {
        "countries": sorted({case["country_code"] for case in cases}),
        "channels": sorted({case["channel"] for case in cases}),
        "languages": sorted({case["language"] for case in cases}),
        "risk_levels": sorted({case["risk_level"] for case in cases}),
        "categories": sorted({case["category"] for case in cases}),
        "permissions": sorted({case["actor_permission"] for case in cases}),
    }
    required = {dimension: sorted(values) for dimension, values in requirements.items()}
    gaps = {
        dimension: sorted(set(required[dimension]) - set(actual[dimension]))
        for dimension in sorted(required)
    }
    return {
        "required": required,
        "actual": actual,
        "gaps": gaps,
        "complete": not any(gaps.values()),
    }


def _bounded_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": report["schema_version"],
        "dataset": report["dataset"],
        "run": report["run"],
        "safety": report["safety"],
        "coverage_complete": report["coverage"]["complete"],
        "coverage_gaps": report["coverage"]["gaps"],
        "case_results": [
            {
                "id": case["id"],
                "category": case["category"],
                "ok": case["ok"],
                "violation_codes": case["actual"]["violation_codes"],
                "mismatches": case["mismatches"],
            }
            for case in report["cases"]
        ],
    }


def _dataset_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
