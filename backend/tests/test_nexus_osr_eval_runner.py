from __future__ import annotations

import copy
import json
from pathlib import Path

from evals.nexus_osr.runner import evaluate_dataset, write_artifacts
from evals.nexus_osr.schema import GovernedDataset, load_dataset


DATASET = (
    Path(__file__).resolve().parents[1]
    / "evals"
    / "nexus_osr"
    / "datasets"
    / "m7-governed-eval-v1.json"
)
REQUIRED_CATEGORY_OUTCOMES = {
    (category, outcome)
    for category in {
        "normal",
        "high_risk",
        "tracking",
        "knowledge",
        "handoff",
        "auto_ticket",
        "governed_tool",
        "unsafe_output",
    }
    for outcome in {"allow", "deny"}
}


def test_deterministic_eval_matches_golden_expectations() -> None:
    dataset = load_dataset(DATASET)

    first = evaluate_dataset(dataset)
    second = evaluate_dataset(dataset)

    assert first == second
    assert first["dataset"]["version"] == "1.1.0"
    assert first["run"]["ok"] is True
    assert first["run"]["case_count"] == 21
    assert first["run"]["failed"] == 0
    assert first["coverage"]["complete"] is True
    assert first["safety"] == {
        "source_classification": "synthetic_redacted",
        "production_payloads_collected": False,
        "customer_visible_behavior_changes": False,
        "messages_sent": 0,
        "tools_executed": 0,
        "production_mutations": 0,
        "raw_case_payloads_emitted": False,
    }


def test_required_scenario_categories_have_positive_and_negative_outcomes() -> None:
    report = evaluate_dataset(load_dataset(DATASET))
    actual_pairs = {
        (case["category"], "allow" if case["actual"]["allowed"] else "deny")
        for case in report["cases"]
    }

    assert REQUIRED_CATEGORY_OUTCOMES <= actual_pairs


def test_runtime_truth_knowledge_handoff_ticket_tool_and_output_boundaries_are_covered() -> None:
    report = evaluate_dataset(load_dataset(DATASET))
    by_id = {case["id"]: case for case in report["cases"]}

    assert by_id["normal_knowledge_answer_allowed"]["actual"]["allowed"] is True
    assert by_id["normal_blocked_reply_denied"]["actual"]["violation_codes"] == [
        "blocked_decision_has_customer_reply"
    ]
    assert by_id["tracking_mcp_current_status_allowed"]["actual"]["allowed"] is True
    assert by_id["tracking_customer_claim_denied"]["actual"]["violation_codes"] == [
        "customer_claim_used_as_fact",
        "tracking_status_without_mcp_current_status",
    ]
    assert by_id["knowledge_customer_visible_allowed"]["actual"]["allowed"] is True
    assert by_id["knowledge_internal_only_denied"]["actual"]["violation_codes"] == [
        "knowledge_answer_without_customer_visible_knowledge"
    ]
    assert by_id["high_risk_handoff_allowed"]["actual"]["allowed"] is True
    assert by_id["handoff_notice_allowed"]["actual"]["allowed"] is True
    assert by_id["handoff_notice_without_handoff_denied"]["actual"]["violation_codes"] == [
        "escalation_without_handoff_or_ticket"
    ]
    assert by_id["auto_ticket_notice_allowed"]["actual"]["allowed"] is True
    assert by_id["auto_ticket_notice_missing_action_denied"]["actual"]["allowed"] is False
    assert by_id["governed_tool_observe_only_allowed"]["actual"]["customer_visible"] is False
    assert by_id["governed_tool_unverified_result_denied"]["actual"]["violation_codes"] == [
        "previous_ai_reply_used_as_fact"
    ]
    assert by_id["unsafe_output_clean_block_allowed"]["actual"]["allowed"] is True
    assert by_id["unsafe_output_clean_block_allowed"]["actual"]["customer_visible"] is False


def test_tenant_permission_and_unsafe_output_cases_fail_closed() -> None:
    report = evaluate_dataset(load_dataset(DATASET))
    by_id = {case["id"]: case for case in report["cases"]}

    assert by_id["tenant_scope_mismatch_denied"]["actual"]["boundary"] == "deny"
    assert by_id["tenant_scope_mismatch_denied"]["actual"]["violation_codes"] == [
        "tenant_scope_mismatch"
    ]
    assert by_id["permission_denied_for_admin_evidence"]["actual"]["violation_codes"] == [
        "permission_denied"
    ]
    assert by_id["unsafe_output_marker_denied"]["actual"]["violation_codes"] == [
        "blocked_decision_has_customer_reply",
        "unsafe_output_marker:credential_marker",
    ]
    assert by_id["unsafe_output_marker_denied"]["actual"]["customer_visible"] is False


def test_coverage_gaps_are_machine_visible() -> None:
    dataset = load_dataset(DATASET)
    payload = copy.deepcopy(dataset.payload)
    payload["coverage_requirements"]["languages"].append("it")
    mutated = GovernedDataset(path=dataset.path, payload=payload)

    report = evaluate_dataset(mutated)

    assert report["run"]["ok"] is False
    assert report["run"]["coverage_failed"] is True
    assert report["coverage"]["gaps"]["languages"] == ["it"]


def test_failure_artifacts_are_bounded_redacted_and_actionable(tmp_path: Path) -> None:
    dataset = load_dataset(DATASET)
    payload = copy.deepcopy(dataset.payload)
    first_summary = next(
        evidence["summary"]
        for case in payload["cases"]
        for evidence in case["decision"]["evidence_sources"]
    )
    first_summary["client_secret"] = "synthetic-private-credential-material"

    expanded_cases = []
    for cycle in range(3):
        for original in payload["cases"]:
            case = copy.deepcopy(original)
            case["id"] = f"{original['id']}_failure_{cycle}"
            case["expected"]["allowed"] = not case["expected"]["allowed"]
            expanded_cases.append(case)
    payload["cases"] = expanded_cases
    mutated = GovernedDataset(path=dataset.path, payload=payload)
    report = evaluate_dataset(mutated)

    manifest = write_artifacts(report, tmp_path, max_artifact_bytes=64 * 1024)
    failures = json.loads((tmp_path / "failures.json").read_text(encoding="utf-8"))
    combined = "\n".join(path.read_text(encoding="utf-8") for path in tmp_path.glob("*.json"))

    assert report["run"]["failed"] > 25
    assert failures["included_count"] == 25
    assert failures["truncated"] is True
    assert manifest["bounded"] is True
    assert manifest["redacted"] is True
    assert "Synthetic blocked response" not in combined
    assert "synthetic-secret-marker" not in combined
    assert "synthetic-private-credential-material" not in combined
    assert "client_secret" not in combined
    assert "evidence_sources" not in combined
    assert "business_reply_type" not in combined
    assert "next_action" not in combined
    assert all(item["bytes"] <= 64 * 1024 for item in manifest["artifacts"])
