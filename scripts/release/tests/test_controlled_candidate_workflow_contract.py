from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = (
    ROOT / ".github/workflows/controlled-candidate-convergence.yml"
).read_text(encoding="utf-8")
CANONICAL = (ROOT / ".github/workflows/canonical-acceptance.yml").read_text(
    encoding="utf-8"
)
COMPOSE = (ROOT / "deploy/docker-compose.controlled.yml").read_text(
    encoding="utf-8"
)
ENV_EXAMPLE = (ROOT / "deploy/.env.controlled.example").read_text(
    encoding="utf-8"
)
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
    def test_runs_only_after_successful_exact_main_acceptance(self) -> None:
        self.assertIn("workflow_run:", WORKFLOW)
        self.assertIn("- Canonical Acceptance", WORKFLOW)
        self.assertIn("- completed", WORKFLOW)
        self.assertNotIn("workflow_dispatch:", WORKFLOW)
        self.assertNotIn("pull_request:", WORKFLOW)
        self.assertNotIn("issue_comment:", WORKFLOW)
        self.assertIn("permissions: {}", WORKFLOW)
        for marker in (
            "github.event.workflow_run.conclusion == 'success'",
            "github.event.workflow_run.event == 'push'",
            "github.event.workflow_run.head_branch == 'main'",
            "CANDIDATE_SHA: ${{ github.event.workflow_run.head_sha }}",
            'test "$TRIGGER_NAME" = "Canonical Acceptance"',
            'test "$TRIGGER_EVENT" = "push"',
            'test "$TRIGGER_BRANCH" = "main"',
            'test "$TRIGGER_CONCLUSION" = "success"',
            'test "$(git rev-parse origin/main)" = "$SOURCE_SHA"',
        ):
            self.assertIn(marker, WORKFLOW)
        self.assertIn(
            "on: {pull_request: {branches: [main]}, push: {branches: [main]}, "
            "workflow_dispatch: {}}",
            CANONICAL,
        )

    def test_actions_are_pinned_and_permissions_are_job_scoped(self) -> None:
        uses = re.findall(r"(?m)^\s*-?\s*uses:\s*([^\s]+)", WORKFLOW)
        self.assertGreaterEqual(len(uses), 10)
        for reference in uses:
            if reference.startswith("./"):
                continue
            self.assertRegex(reference, r"@[0-9a-f]{40}$")
        for mutable in ("@main", "@master", "@v1", "@v2", "@v3", "@v4"):
            self.assertNotIn(mutable, WORKFLOW)
        self.assertIn("packages: write", WORKFLOW)
        self.assertIn("attestations: write", WORKFLOW)
        self.assertIn("id-token: write", WORKFLOW)

    def test_existing_rc_build_is_reused_and_no_second_build_exists(self) -> None:
        combined = WORKFLOW + "\n" + HELPERS
        self.assertIn("scripts/release/run_rc_test_candidate.sh", combined)
        self.assertNotIn("docker build ", WORKFLOW)
        self.assertIn('docker tag "${CANDIDATE_IMAGE}"', combined)
        self.assertIn('docker push "${registry_image}:${tag}"', combined)
        self.assertIn(
            'test "${pulled_image_id}" = "${local_image_id}"',
            combined,
        )

    def test_failure_evidence_is_bounded_and_blocks_publication(self) -> None:
        for marker in (
            "id: controlled_rc",
            "capture_controlled_rc_failure.py",
            "steps.controlled_rc.outcome == 'failure'",
            "controlled-rc-failure-${{ env.CANDIDATE_SHA }}",
            "id: image_assurance",
            "capture_controlled_image_assurance_failure.py",
            "steps.image_assurance.outcome == 'failure'",
            "controlled-image-assurance-failure-${{ env.CANDIDATE_SHA }}",
        ):
            self.assertIn(marker, WORKFLOW)
        self.assertLess(
            WORKFLOW.index("Upload bounded RC failure evidence"),
            WORKFLOW.index("Verify runtime imports"),
        )
        self.assertLess(
            WORKFLOW.index("Upload bounded image-assurance failure evidence"),
            WORKFLOW.index("Publish and pull back the assured binary"),
        )

    def test_same_binary_recovery_and_provenance_are_bound(self) -> None:
        combined = WORKFLOW + "\n" + HELPERS
        for marker in (
            "image-ref: ${{ env.CANDIDATE_IMAGE }}",
            "image: ${{ env.CANDIDATE_IMAGE }}",
            "release-image-manifest.json",
            "registry-publish-receipt.json",
            "scripts/qualification/recovery/run_recovery_qualification.sh",
            "actions/attest-build-provenance@"
            "0f67c3f4856b2e3261c31976d6725780e5e4c373",
            "subject-digest: ${{ steps.identity.outputs.digest }}",
            "push-to-registry: true",
            "create-storage-record: false",
        ):
            self.assertIn(marker, combined)
        login = WORKFLOW.index("Authenticate GHCR for registry attestation")
        attest = WORKFLOW.index("Attest exact registry digest")
        logout = WORKFLOW.index("Clear GHCR registry credentials")
        finalize = WORKFLOW.index("Build final evidence-bound candidate")
        self.assertLess(login, attest)
        self.assertLess(attest, logout)
        self.assertLess(logout, finalize)
        self.assertIn("docker logout ghcr.io", WORKFLOW)

    def test_final_artifact_binds_acceptance_and_renders_server_identity(self) -> None:
        for marker in (
            "nexus.canonical-acceptance-receipt.v1",
            "CANONICAL_ACCEPTANCE_RUN_ID",
            "CANONICAL_ACCEPTANCE_RUN_URL",
            "controlled-candidate.env",
            "CONTROLLED_IMAGE=${image}",
            "GIT_SHA=${SOURCE_SHA}",
            "FRONTEND_BUILD_SHA=",
            "EXPECTED_MIGRATION_HEAD=",
            "ACTIVATION_EVIDENCE_SOURCE_SHA=${SOURCE_SHA}",
            "ACTIVATION_EVIDENCE_IMAGE_DIGEST=${digest}",
            'rm -f "$FINAL_DIR/artifact-scan.json"',
            "scan_controlled_candidate_artifacts.py",
        ):
            self.assertIn(marker, WORKFLOW)

    def test_controlled_candidate_remains_fail_closed_for_external_effects(
        self,
    ) -> None:
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
        self.assertIn(
            "- Controlled deployment performed: `false`",
            WORKFLOW,
        )
        self.assertIn("- External effects authorized: `false`", WORKFLOW)

    def test_controlled_compose_is_digest_only_and_has_no_external_sidecars(
        self,
    ) -> None:
        self.assertIn("${CONTROLLED_IMAGE:?", COMPOSE)
        self.assertIn("${NEXUS_RUNTIME_SECRETS_HOST_PATH:?", COMPOSE)
        self.assertNotRegex(COMPOSE, r"(?m)^\s*build\s*:")
        self.assertNotIn(":latest", COMPOSE)
        self.assertNotIn("external: true", COMPOSE)
        self.assertNotIn("production_runtime", COMPOSE)
        self.assertNotIn("whatsapp-sidecar", COMPOSE)
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
