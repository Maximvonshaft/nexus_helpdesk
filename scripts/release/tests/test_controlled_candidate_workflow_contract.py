from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "canonical-acceptance.yml"
WORKFLOW = WORKFLOW_PATH.read_text(encoding="utf-8")
COMPOSE = (ROOT / "deploy" / "docker-compose.controlled.yml").read_text(encoding="utf-8")
ENV_EXAMPLE = (ROOT / "deploy" / ".env.controlled.example").read_text(encoding="utf-8")
HELPERS = "\n".join(
    (ROOT / path).read_text(encoding="utf-8")
    for path in (
        "scripts/release/run_controlled_rc_gate.sh",
        "scripts/release/run_controlled_image_assurance.sh",
        "scripts/release/publish_controlled_image.sh",
        "scripts/release/finalize_controlled_candidate.sh",
        "scripts/release/run_controlled_recovery_gate.sh",
        "scripts/release/build_controlled_candidate_manifest.py",
        "scripts/release/capture_controlled_image_assurance_failure.py",
        "scripts/deploy/validate_controlled_server_preflight.py",
    )
)


class ControlledCandidateWorkflowContractTests(unittest.TestCase):
    def test_one_canonical_workflow_owns_acceptance_and_main_publication(self) -> None:
        workflows = sorted(
            path.name
            for path in (ROOT / ".github" / "workflows").iterdir()
            if path.is_file()
        )
        self.assertEqual(workflows, ["canonical-acceptance.yml"])
        self.assertIn("pull_request:", WORKFLOW)
        self.assertIn("push:", WORKFLOW)
        self.assertIn("workflow_dispatch:", WORKFLOW)
        for job in (
            "required-gate:",
            "controlled-build-assure-publish:",
            "controlled-recovery:",
            "controlled-bind-attest:",
        ):
            self.assertIn(job, WORKFLOW)
        self.assertNotIn("controlled-candidate-dispatch-bridge", WORKFLOW)
        self.assertFalse((ROOT / ".github" / "controlled-candidate-request.json").exists())

    def test_release_jobs_are_exact_main_post_gate_and_least_privilege(self) -> None:
        self.assertGreaterEqual(WORKFLOW.count("needs.required-gate.result == 'success'"), 2)
        self.assertGreaterEqual(WORKFLOW.count("github.ref == 'refs/heads/main'"), 3)
        self.assertIn("packages: write", WORKFLOW)
        self.assertIn("attestations: write", WORKFLOW)
        self.assertIn("id-token: write", WORKFLOW)
        self.assertIn("issues: write", WORKFLOW)
        self.assertNotIn("secrets.", WORKFLOW)
        self.assertNotIn("continue-on-error: true", WORKFLOW)

    def test_actions_are_pinned_and_no_mutable_action_tags(self) -> None:
        uses = re.findall(r"(?m)^\s*-?\s*uses:\s*([^\s]+)", WORKFLOW)
        self.assertGreaterEqual(len(uses), 20)
        for reference in uses:
            if reference.startswith("./"):
                continue
            self.assertRegex(reference, r"@[0-9a-f]{40}$")
        for mutable in ("@main", "@master", "@v1", "@v2", "@v3", "@v4"):
            self.assertNotIn(mutable, WORKFLOW)

    def test_existing_rc_build_is_reused_inside_publication_phase(self) -> None:
        release_block = WORKFLOW.split("  controlled-build-assure-publish:", 1)[1].split(
            "  controlled-recovery:", 1
        )[0]
        combined = release_block + "\n" + HELPERS
        self.assertIn("scripts/release/run_controlled_rc_gate.sh", combined)
        self.assertNotIn("docker build ", release_block)
        self.assertIn('docker tag "${CANDIDATE_IMAGE}"', combined)
        self.assertIn('docker push "${registry_image}:${tag}"', combined)
        self.assertIn('test "${pulled_image_id}" = "${local_image_id}"', combined)

    def test_failed_rc_and_image_assurance_block_publication(self) -> None:
        self.assertIn("id: controlled_rc", WORKFLOW)
        self.assertIn("capture_controlled_rc_failure.py", WORKFLOW)
        self.assertIn("steps.controlled_rc.outcome == 'failure'", WORKFLOW)
        self.assertIn(
            "controlled-rc-failure-${{ needs.candidate-identity.outputs.source_sha }}",
            WORKFLOW,
        )
        self.assertIn("id: image_assurance", WORKFLOW)
        self.assertIn("capture_controlled_image_assurance_failure.py", WORKFLOW)
        self.assertIn("steps.image_assurance.outcome == 'failure'", WORKFLOW)
        self.assertIn(
            "controlled-image-assurance-failure-${{ needs.candidate-identity.outputs.source_sha }}",
            WORKFLOW,
        )
        self.assertLess(
            WORKFLOW.index("Upload bounded image-assurance failure evidence"),
            WORKFLOW.index("Publish and pull back the assured binary"),
        )

    def test_same_binary_provenance_and_recovery_are_bound(self) -> None:
        combined = WORKFLOW + "\n" + HELPERS
        self.assertIn("release-image-manifest.json", combined)
        self.assertIn("registry-publish-receipt.json", combined)
        self.assertIn("LOCAL_IMAGE_ENV_JSON", combined)
        self.assertIn("PULLED_IMAGE_ENV_JSON", combined)
        self.assertIn(
            "actions/attest-build-provenance@0f67c3f4856b2e3261c31976d6725780e5e4c373",
            WORKFLOW,
        )
        self.assertIn("subject-digest: ${{ steps.identity.outputs.digest }}", WORKFLOW)
        self.assertIn("push-to-registry: true", WORKFLOW)
        self.assertIn("scripts/release/run_controlled_recovery_gate.sh", combined)
        self.assertIn(
            "controlled-recovery-${{ needs.candidate-identity.outputs.source_sha }}",
            WORKFLOW,
        )
        self.assertIn(
            "controlled-candidate-${{ needs.candidate-identity.outputs.source_sha }}",
            WORKFLOW,
        )

    def test_release_trace_is_bounded_and_does_not_claim_deployment(self) -> None:
        self.assertIn('"repos/${GH_REPO}/issues/724/comments"', WORKFLOW)
        self.assertIn("## CONTROLLED_CANDIDATE_PUBLISHED", WORKFLOW)
        for marker in ("- Source:", "- Image:", "- Migration:", "- Run:", "- URL:"):
            self.assertIn(marker, WORKFLOW)
        self.assertIn("- Production ready: `false`", WORKFLOW)
        self.assertIn("- Deployment performed: `false`", WORKFLOW)
        self.assertIn("- External effects authorized: `false`", WORKFLOW)

    def test_external_effect_safety_and_controlled_topology_are_preserved(self) -> None:
        for marker in (
            "PROVIDER_RUNTIME_KILL_SWITCH=true",
            "PROVIDER_RUNTIME_CANARY_PERCENT=0",
            "ENABLE_OUTBOUND_DISPATCH=false",
            "WHATSAPP_NATIVE_ENABLED=false",
            "SPEEDAF_WORK_ORDER_CREATE_ENABLED=false",
            "OPERATIONS_DISPATCH_MODE=disabled",
            "ALLOW_DEV_AUTH=false",
            "LOCAL_STORAGE_BACKUP_REQUIRED=true",
        ):
            self.assertIn(marker, ENV_EXAMPLE)

        for marker in (
            "${CONTROLLED_IMAGE:?",
            "${SECRET_KEY:?set Web JWT secret}",
            "${METRICS_TOKEN:?set dedicated metrics token}",
            "${NEXUS_UPLOADS_HOST_PATH:?set uploads path}",
            "${NEXUS_UPLOAD_BACKUP_HOST_PATH:?set upload backup path}",
        ):
            self.assertIn(marker, COMPOSE)
        self.assertNotRegex(COMPOSE, r"(?m)^\s*build\s*:")
        self.assertNotIn(":latest", COMPOSE)
        self.assertNotIn("external: true", COMPOSE)
        for service in (
            "migrate-controlled:",
            "app-controlled:",
            "worker-outbound-controlled:",
            "worker-background-controlled:",
            "worker-webchat-ai-controlled:",
            "worker-handoff-snapshot-controlled:",
        ):
            self.assertIn(service, COMPOSE)


if __name__ == "__main__":
    unittest.main()
