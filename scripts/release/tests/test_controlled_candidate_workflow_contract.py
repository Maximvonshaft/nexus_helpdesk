from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
WORKFLOW_PATH = WORKFLOW_DIR / "canonical-acceptance.yml"
WORKFLOW = WORKFLOW_PATH.read_text(encoding="utf-8")
HELPERS = "\n".join(
    (ROOT / path).read_text(encoding="utf-8")
    for path in (
        "scripts/release/run_controlled_image_assurance.sh",
        "scripts/release/publish_controlled_image.sh",
        "scripts/release/finalize_controlled_candidate.sh",
        "scripts/release/run_controlled_rc_gate.sh",
        "scripts/release/run_controlled_recovery_gate.sh",
        "scripts/release/build_controlled_candidate_manifest.py",
        "scripts/release/capture_controlled_image_assurance_failure.py",
        "scripts/deploy/validate_controlled_server_preflight.py",
    )
)


class ControlledCandidateWorkflowContractTests(unittest.TestCase):
    def test_one_workflow_contains_acceptance_and_main_publication(self) -> None:
        workflow_files = sorted(path.name for path in WORKFLOW_DIR.glob("*") if path.is_file())
        self.assertEqual(workflow_files, ["canonical-acceptance.yml"])
        self.assertIn("name: Canonical Acceptance", WORKFLOW)
        self.assertIn("controlled-build-publish:", WORKFLOW)
        self.assertIn("controlled-recovery:", WORKFLOW)
        self.assertIn("controlled-bind-attest:", WORKFLOW)

    def test_publication_is_main_push_only_and_after_required_gate(self) -> None:
        for marker in (
            "github.event_name == 'push'",
            "github.ref == 'refs/heads/main'",
            "needs.required-gate.result == 'success'",
            "needs: [candidate-identity, required-gate]",
            "test \"$(git rev-parse origin/main)\" = \"$SOURCE_SHA\"",
        ):
            self.assertIn(marker, WORKFLOW)
        self.assertNotIn("controlled-candidate-dispatch-bridge", WORKFLOW)
        self.assertNotIn("controlled-candidate-request.json", WORKFLOW)

    def test_actions_are_pinned_and_runner_is_fixed(self) -> None:
        uses = re.findall(r"(?m)^\s*-?\s*uses:\s*([^\s]+)", WORKFLOW)
        self.assertGreaterEqual(len(uses), 20)
        for reference in uses:
            if reference.startswith("./"):
                continue
            self.assertRegex(reference, r"@[0-9a-f]{40}$")
        for mutable in ("@main", "@master", "@v1", "@v2", "@v3", "@v4"):
            self.assertNotIn(mutable, WORKFLOW)
        self.assertNotIn("ubuntu-latest", WORKFLOW)
        self.assertIn("runs-on: ubuntu-24.04", WORKFLOW)

    def test_publication_reuses_one_rc_build_and_verifies_pullback_identity(self) -> None:
        combined = WORKFLOW + "\n" + HELPERS
        self.assertIn("scripts/release/run_controlled_rc_gate.sh", combined)
        publication = WORKFLOW.split("controlled-build-publish:", 1)[1].split(
            "controlled-recovery:", 1
        )[0]
        self.assertNotIn("docker build ", publication)
        self.assertIn("scripts/release/publish_controlled_image.sh", publication)
        self.assertIn('docker tag "${CANDIDATE_IMAGE}"', combined)
        self.assertIn('docker push "${registry_image}:${tag}"', combined)
        self.assertIn('test "${pulled_image_id}" = "${local_image_id}"', combined)

    def test_bounded_failure_evidence_blocks_publication(self) -> None:
        self.assertIn("id: controlled_rc", WORKFLOW)
        self.assertIn("capture_controlled_rc_failure.py", WORKFLOW)
        self.assertIn("steps.controlled_rc.outcome == 'failure'", WORKFLOW)
        self.assertIn("controlled-rc-failure-${{ needs.candidate-identity.outputs.source_sha }}", WORKFLOW)
        self.assertIn("id: image_assurance", WORKFLOW)
        self.assertIn("capture_controlled_image_assurance_failure.py", WORKFLOW)
        self.assertIn("steps.image_assurance.outcome == 'failure'", WORKFLOW)
        self.assertIn(
            "controlled-image-assurance-failure-${{ needs.candidate-identity.outputs.source_sha }}",
            WORKFLOW,
        )

    def test_recovery_attestation_and_final_manifest_are_mandatory(self) -> None:
        combined = WORKFLOW + "\n" + HELPERS
        for marker in (
            "scripts/release/run_controlled_recovery_gate.sh",
            "controlled-recovery-${{ needs.candidate-identity.outputs.source_sha }}",
            "actions/attest-build-provenance@0f67c3f4856b2e3261c31976d6725780e5e4c373",
            "subject-digest: ${{ steps.identity.outputs.digest }}",
            "push-to-registry: true",
            "scripts/release/finalize_controlled_candidate.sh",
            "controlled-candidate-${{ needs.candidate-identity.outputs.source_sha }}",
            '"repos/${GH_REPO}/issues/724/comments"',
        ):
            self.assertIn(marker, combined)

    def test_publication_never_authorizes_deployment_or_external_effects(self) -> None:
        controlled_env = (ROOT / "deploy/.env.controlled.example").read_text(encoding="utf-8")
        for marker in (
            "PROVIDER_RUNTIME_KILL_SWITCH=true",
            "PROVIDER_RUNTIME_CANARY_PERCENT=0",
            "ENABLE_OUTBOUND_DISPATCH=false",
            "WHATSAPP_NATIVE_ENABLED=false",
            "SPEEDAF_WORK_ORDER_CREATE_ENABLED=false",
            "OPERATIONS_DISPATCH_MODE=disabled",
            "ALLOW_DEV_AUTH=false",
        ):
            self.assertIn(marker, controlled_env)
        self.assertIn("- Production ready: `false`.", WORKFLOW)
        self.assertIn("- Deployment performed: `false`.", WORKFLOW)
        self.assertIn("- External effects authorized: `false`.", WORKFLOW)


if __name__ == "__main__":
    unittest.main()
