import json
import py_compile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DELIVERY_WORKFLOW = ROOT / "config" / "governance" / "delivery-workflow.v1.json"
CANONICAL_WORKFLOW = ROOT / ".github" / "workflows" / "canonical-acceptance.yml"
CONTROLLED_CONTRACT = (
    ROOT / "scripts" / "release" / "tests" / "test_controlled_candidate_workflow_contract.py"
)
GENERATED_OUTPUT = Path("/tmp/nexus-development/generated-rc-authority")


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


def test_materialize_rc_browser_authority_boundary_patch():
    canonical = CANONICAL_WORKFLOW.read_text(encoding="utf-8")
    stale_env = '        env:\n          RC_RUN_BROWSER_SMOKE: "true"\n'
    assert canonical.count(stale_env) == 1
    canonical = canonical.replace(stale_env, "")
    assert "RC_RUN_BROWSER_SMOKE" not in canonical

    contract = CONTROLLED_CONTRACT.read_text(encoding="utf-8")
    insertion_point = "\n    def test_canonical_acceptance_treats_sidecar_as_first_class_supply_chain(\n"
    assert contract.count(insertion_point) == 1
    authority_test = '''
    def test_live_browser_journey_has_one_runtime_authority(self) -> None:
        rc_runner = (
            ROOT / "scripts/release/run_rc_test_candidate.sh"
        ).read_text(encoding="utf-8")
        frontend_job = CANONICAL[
            CANONICAL.index("  frontend-browser:") :
            CANONICAL.index("  postgres-acceptance:")
        ]

        self.assertNotIn("RC_RUN_BROWSER_SMOKE", frontend_job)
        for marker in (
            'if [[ "${RC_RUN_BROWSER_SMOKE:-false}" =~',
            'PLAYWRIGHT_BASE_URL="${BASE_URL}"',
            'RC_TEST_ADMIN_USERNAME="${RC_TEST_ADMIN_USERNAME}"',
            'RC_TEST_ADMIN_PASSWORD="${RC_TEST_ADMIN_PASSWORD}"',
            'RC_SOURCE_SHA="${SOURCE_SHA}"',
            "npm run e2e -- e2e/rc-live.spec.ts --workers=1 --reporter=line",
            "RC_RUN_BROWSER_SMOKE must be true for a deployable candidate",
        ):
            self.assertIn(marker, rc_runner)
        self.assertEqual(rc_runner.count("e2e/rc-live.spec.ts"), 1)

'''
    contract = contract.replace(insertion_point, "\n" + authority_test + insertion_point)

    generated_canonical = GENERATED_OUTPUT / "canonical-acceptance.yml"
    generated_contract = (
        GENERATED_OUTPUT
        / "scripts/release/tests/test_controlled_candidate_workflow_contract.py"
    )
    generated_canonical.parent.mkdir(parents=True, exist_ok=True)
    generated_contract.parent.mkdir(parents=True, exist_ok=True)
    generated_canonical.write_text(canonical, encoding="utf-8")
    generated_contract.write_text(contract, encoding="utf-8")

    py_compile.compile(str(generated_contract), doraise=True)
    assert generated_canonical.read_text(encoding="utf-8").count("frontend-browser:") == 1
    assert generated_contract.read_text(encoding="utf-8").count(
        "def test_live_browser_journey_has_one_runtime_authority"
    ) == 1
