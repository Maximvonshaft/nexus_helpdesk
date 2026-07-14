from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = (ROOT / ".github/workflows/controlled-candidate-convergence.yml").read_text(encoding="utf-8")
COMPOSE = (ROOT / "deploy/docker-compose.controlled.yml").read_text(encoding="utf-8")
ENV_EXAMPLE = (ROOT / "deploy/.env.controlled.example").read_text(encoding="utf-8")
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
    def test_manual_main_only_and_least_privilege(self) -> None:
        self.assertIn("workflow_dispatch:", WORKFLOW)
        self.assertNotIn("pull_request:", WORKFLOW)
        self.assertNotIn("push:\n", WORKFLOW)
        self.assertIn("permissions: {}", WORKFLOW)
        self.assertIn("guard-main:", WORKFLOW)
        self.assertIn('test "$GITHUB_REF" = "refs/heads/main"', WORKFLOW)
        self.assertGreaterEqual(WORKFLOW.count("needs: guard-main"), 2)
        self.assertIn("if: github.ref == 'refs/heads/main'", WORKFLOW)
        self.assertIn("packages: write", WORKFLOW)
        self.assertIn("attestations: write", WORKFLOW)
        self.assertIn("id-token: write", WORKFLOW)

    def test_actions_are_pinned_and_no_mutable_action_tags(self) -> None:
        uses = re.findall(r"(?m)^\s*-?\s*uses:\s*([^\s]+)", WORKFLOW)
        self.assertGreaterEqual(len(uses), 8)
        for reference in uses:
            if reference.startswith("./"):
                continue
            self.assertRegex(reference, r"@[0-9a-f]{40}$")
        for mutable in ("@main", "@master", "@v1", "@v2", "@v3", "@v4"):
            self.assertNotIn(mutable, WORKFLOW)

    def test_existing_rc_build_is_reused_and_no_second_build_exists(self) -> None:
        combined = WORKFLOW + "\n" + HELPERS
        self.assertIn("scripts/release/run_rc_test_candidate.sh", combined)
        self.assertNotIn("docker build ", WORKFLOW)
        self.assertIn('docker tag "${CANDIDATE_IMAGE}"', combined)
        self.assertIn('docker push "${registry_image}:${tag}"', combined)
        self.assertIn('test "${pulled_image_id}" = "${local_image_id}"', combined)

    def test_failed_rc_is_bounded_and_blocks_publication(self) -> None:
        self.assertIn("id: controlled_rc", WORKFLOW)
        self.assertIn("capture_controlled_rc_failure.py", WORKFLOW)
        self.assertIn("steps.controlled_rc.outcome == 'failure'", WORKFLOW)
        self.assertIn("controlled-rc-failure-${{ github.sha }}", WORKFLOW)
        self.assertIn("steps.rc_failure_scan.outcome == 'success'", WORKFLOW)
        self.assertIn('exit "${code}"', WORKFLOW)
        self.assertIn("path: artifacts/controlled-rc-failure", WORKFLOW)
        self.assertLess(
            WORKFLOW.index("Upload bounded RC failure evidence"),
            WORKFLOW.index("Verify runtime imports"),
        )
        failure_upload_block = WORKFLOW.split("- name: Upload bounded RC failure evidence", 1)[1].split(
            "- name: Verify runtime imports", 1
        )[0]
        self.assertNotIn("RUNNER_TEMP", failure_upload_block)
        self.assertNotIn("controlled-rc-run.log", failure_upload_block)

    def test_failed_image_assurance_is_bounded_and_blocks_publication(self) -> None:
        self.assertIn("id: image_assurance", WORKFLOW)
        self.assertIn("capture_controlled_image_assurance_failure.py", WORKFLOW)
        self.assertIn("steps.image_assurance.outcome == 'failure'", WORKFLOW)
        self.assertIn("controlled-image-assurance-failure-${{ github.sha }}", WORKFLOW)
        self.assertIn("steps.assurance_failure_scan.outcome == 'success'", WORKFLOW)
        self.assertIn("path: artifacts/controlled-image-assurance-failure", WORKFLOW)
        self.assertLess(
            WORKFLOW.index("Upload bounded image-assurance failure evidence"),
            WORKFLOW.index("Publish and pull back the assured binary"),
        )
        failure_block = WORKFLOW.split("- name: Build and scan bounded image-assurance failure evidence", 1)[1].split(
            "- name: Publish and pull back the assured binary", 1
        )[0]
        for forbidden in (
            "trivy.raw.json",
            "image.raw.cdx.json",
            "frontend.raw.cdx.json",
            "installed-license-evidence.json",
            "release-image-manifest.json",
        ):
            self.assertNotIn(forbidden, failure_block)
        self.assertIn('exit "${code}"', WORKFLOW)

    def test_same_binary_and_build_metadata_are_bound(self) -> None:
        combined = WORKFLOW + "\n" + HELPERS
        self.assertIn("image-ref: ${{ env.CANDIDATE_IMAGE }}", WORKFLOW)
        self.assertIn("image: ${{ env.CANDIDATE_IMAGE }}", WORKFLOW)
        self.assertIn("release-image-manifest.json", combined)
        self.assertIn("registry-publish-receipt.json", WORKFLOW)
        self.assertIn("LOCAL_IMAGE_ENV_JSON", combined)
        self.assertIn("PULLED_IMAGE_ENV_JSON", combined)
        self.assertIn('"build_time": local_env["BUILD_TIME"]', combined)
        self.assertIn('"app_version": local_env["APP_VERSION"]', combined)
        self.assertIn("publish_receipt_build_time_invalid", combined)
        self.assertIn("actions/attest-build-provenance@0f67c3f4856b2e3261c31976d6725780e5e4c373", WORKFLOW)
        self.assertIn("subject-digest: ${{ steps.identity.outputs.digest }}", WORKFLOW)
        self.assertIn("push-to-registry: true", WORKFLOW)

    def test_registry_attestation_is_authenticated_without_unsupported_storage_record(self) -> None:
        login = WORKFLOW.index("Authenticate GHCR for registry attestation")
        attest = WORKFLOW.index("Attest exact registry digest")
        logout = WORKFLOW.index("Clear GHCR registry credentials")
        finalize = WORKFLOW.index("Build final evidence-bound candidate")
        self.assertLess(login, attest)
        self.assertLess(attest, logout)
        self.assertLess(logout, finalize)
        self.assertIn("GHCR_TOKEN: ${{ github.token }}", WORKFLOW)
        self.assertIn(
            'printf \'%s\' "$GHCR_TOKEN" | docker login ghcr.io --username "$GITHUB_ACTOR" --password-stdin',
            WORKFLOW,
        )
        self.assertIn("create-storage-record: false", WORKFLOW)
        self.assertNotIn("artifact-metadata: write", WORKFLOW)
        self.assertIn("if: ${{ always() }}", WORKFLOW)
        self.assertIn("docker logout ghcr.io", WORKFLOW)

    def test_recovery_and_external_effect_safety_are_required(self) -> None:
        self.assertIn("scripts/qualification/recovery/run_recovery_qualification.sh", WORKFLOW + "\n" + HELPERS)
        self.assertIn("controlled-recovery-${{ github.sha }}", WORKFLOW)
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

    def test_controlled_compose_is_digest_only_and_has_no_external_sidecars(self) -> None:
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
