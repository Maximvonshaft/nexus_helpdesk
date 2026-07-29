import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DELIVERY_WORKFLOW = ROOT / "config" / "governance" / "delivery-workflow.v1.json"
CANONICAL_WORKFLOW = ROOT / ".github" / "workflows" / "canonical-acceptance.yml"


def test_delivery_workflow_contract_preserves_one_large_pr_and_one_writer():
    contract = json.loads(DELIVERY_WORKFLOW.read_text(encoding="utf-8"))

    assert contract["schema"] == "nexus.delivery-workflow.v1"
    assert contract["authority_issue"] == 722
    assert contract["writer_lease"]["cardinality"] == "one_active_writer_per_root_cause"
    assert contract["pull_request_model"]["large_pr_allowed"] is True
    assert contract["pull_request_model"]["split_required"] is False
    assert contract["pull_request_model"]["draft_is_development_state"] is True
    assert contract["pull_request_model"]["ready_for_review_is_candidate_state"] is True


def test_delivery_workflow_contract_defines_three_fail_closed_validation_modes():
    contract = json.loads(DELIVERY_WORKFLOW.read_text(encoding="utf-8"))
    modes = contract["validation_modes"]

    assert set(modes) == {"development", "candidate", "main"}
    assert modes["development"]["full_acceptance_allowed"] is False
    assert modes["development"]["main_sync_required"] is False
    assert modes["candidate"]["full_acceptance_allowed"] is True
    assert modes["candidate"]["main_sync_required"] is True
    assert modes["candidate"]["head_freeze_required"] is True
    assert modes["main"]["full_acceptance_allowed"] is True


def test_canonical_workflow_routes_draft_and_candidate_without_second_authority():
    workflow = CANONICAL_WORKFLOW.read_text(encoding="utf-8")

    for marker in (
        "validation-mode:",
        "development-fast:",
        "ready_for_review",
        "converted_to_draft",
        "needs.validation-mode.outputs.mode == 'development'",
        "needs.validation-mode.outputs.run_full == 'true'",
        'case "$MODE" in',
        "development)",
        "candidate|main)",
    ):
        assert marker in workflow

    assert workflow.count("required-gate:") == 1
    assert "pull_request_target:" not in workflow
    assert "workflow_run:" not in workflow
