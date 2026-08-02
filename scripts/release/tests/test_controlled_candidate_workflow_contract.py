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
        "scripts/release/require_exact_current_main.sh",
        "scripts/release/build_controlled_candidate_manifest.py",
        "scripts/release/capture_controlled_image_assurance_failure.py",
        "scripts/deploy/validate_controlled_server_preflight.py",
    )
)


class ControlledCandidateWorkflowContractTests(unittest.TestCase):
    def test_application_sbom_uses_the_assurance_authority_trivy_contract(self) -> None:
        step = WORKFLOW[
            WORKFLOW.index("Generate same-image CycloneDX with immutable Trivy action") :
            WORKFLOW.index("Evaluate image assurance and compliance")
        ]
        self.assertIn(
            "uses: aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25",
            step,
        )
        for marker in (
            "image-ref: ${{ env.CANDIDATE_IMAGE }}",
            "format: cyclonedx",
            "output: artifacts/release-image/image.raw.cdx.json",
            "scanners: vuln",
        ):
            self.assertIn(marker, step)
        self.assertNotIn("anchore/sbom-action", WORKFLOW)

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
            "CANDIDATE_SHA: ${{ github.event.workflow_run.head_sha }}",
            'test "$TRIGGER_NAME" = "Canonical Acceptance"',
            'test "$TRIGGER_EVENT" = "push"',
            'test "$TRIGGER_BRANCH" = "main"',
            'test "$TRIGGER_CONCLUSION" = "success"',
            'test "$(git rev-parse origin/main)" = "$SOURCE_SHA"',
        ):
            self.assertIn(marker, WORKFLOW)
        for marker in (
            "pull_request:",
            "types: [opened, synchronize, reopened, ready_for_review, converted_to_draft]",
            "push:",
            "workflow_dispatch: {}",
            "validation-mode:",
            "development-fast:",
        ):
            self.assertIn(marker, CANONICAL)

    def test_guard_runs_for_successful_push_and_checks_main_inside_job(self) -> None:
        guard = WORKFLOW[
            WORKFLOW.index("  guard-main:") : WORKFLOW.index("  build-assure-publish:")
        ]
        job_if = guard[
            guard.index("    if: >-") : guard.index("    permissions:")
        ]
        self.assertIn(
            "github.event.workflow_run.conclusion == 'success'",
            job_if,
        )
        self.assertIn("github.event.workflow_run.event == 'push'", job_if)
        self.assertNotIn("github.event.workflow_run.head_branch", job_if)
        self.assertIn('test "$TRIGGER_BRANCH" = "main"', guard)
        self.assertIn(
            'test "$(git rev-parse origin/main)" = "$SOURCE_SHA"',
            guard,
        )

    def test_actions_are_pinned_and_permissions_are_job_scoped(self) -> None:
        uses = re.findall(r"(?m)^\s*-?\s*uses:\s*([^\s]+)", WORKFLOW)
        self.assertGreaterEqual(len(uses), 12)
        for reference in uses:
            if reference.startswith("./"):
                continue
            self.assertRegex(reference, r"@[0-9a-f]{40}$")
        for mutable in ("@main", "@master", "@v1", "@v2", "@v3", "@v4"):
            self.assertNotIn(mutable, WORKFLOW)
        self.assertIn("packages: write", WORKFLOW)
        self.assertIn("attestations: write", WORKFLOW)
        self.assertIn("id-token: write", WORKFLOW)

    def test_job_level_environment_uses_github_context_directly(self) -> None:
        illegal = re.findall(
            r"(?m)^ {6}[A-Z][A-Z0-9_]*:.*\$\{\{\s*env\.",
            WORKFLOW,
        )
        self.assertEqual(illegal, [])
        for marker in (
            "SOURCE_SHA: ${{ github.event.workflow_run.head_sha }}",
            "RC_SOURCE_SHA: ${{ github.event.workflow_run.head_sha }}",
            "CANDIDATE_IMAGE: nexusdesk/helpdesk:rc-test-${{ github.event.workflow_run.head_sha }}",
            "SIDECAR_IMAGE: nexusdesk/whatsapp-sidecar:controlled-${{ github.event.workflow_run.head_sha }}",
        ):
            self.assertIn(marker, WORKFLOW)

    def test_application_rc_is_reused_and_sidecar_is_built_exactly_once(self) -> None:
        combined = WORKFLOW + "\n" + HELPERS
        self.assertIn("scripts/release/run_rc_test_candidate.sh", combined)
        self.assertEqual(WORKFLOW.count("docker build --pull=false"), 1)
        self.assertIn(
            "--file connectors/whatsapp-sidecar/Dockerfile",
            WORKFLOW,
        )
        self.assertIn("Build exact-main WhatsApp Sidecar image once", WORKFLOW)
        self.assertIn("publish_and_pull()", HELPERS)
        self.assertEqual(HELPERS.count("publish_and_pull \\"), 2)
        self.assertIn('test "${pulled_image_id}" = "${expected_local_id}"', HELPERS)
        self.assertIn("whatsapp-sidecar-registry-publish-receipt.json", combined)

    def test_distroless_runtime_smoke_does_not_require_a_shell(self) -> None:
        self.assertNotIn(
            'docker run --rm --entrypoint sh "$CANDIDATE_IMAGE"',
            WORKFLOW,
        )
        self.assertEqual(
            WORKFLOW.count(
                'docker run --rm --entrypoint /usr/local/bin/python "$CANDIDATE_IMAGE"'
            ),
            2,
        )
        self.assertIn(
            "-c 'import app.main, psycopg, cryptography, argon2'",
            WORKFLOW,
        )
        self.assertIn("-m gunicorn --check-config app.main:app", WORKFLOW)

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
            WORKFLOW.index("Publish and pull back both assured binaries"),
        )

    def test_irreversible_steps_recheck_exact_current_main(self) -> None:
        guard = "bash scripts/release/require_exact_current_main.sh"
        self.assertEqual(WORKFLOW.count(guard), 2)
        publish_guard = WORKFLOW.index(
            "Reconfirm exact current main before registry publication"
        )
        publish = WORKFLOW.index("Publish and pull back both assured binaries")
        attest_guard = WORKFLOW.index(
            "Reconfirm exact current main before provenance attestation"
        )
        login = WORKFLOW.index("Authenticate GHCR for registry attestation")
        attest_application = WORKFLOW.index(
            "Attest exact application registry digest"
        )
        attest_sidecar = WORKFLOW.index(
            "Attest exact WhatsApp Sidecar registry digest"
        )
        self.assertLess(publish_guard, publish)
        self.assertLess(attest_guard, login)
        self.assertLess(login, attest_application)
        self.assertLess(attest_application, attest_sidecar)
        for marker in (
            ': "${SOURCE_SHA:?SOURCE_SHA required}"',
            "git fetch --no-tags origin main",
            "git rev-parse origin/main",
            "git diff --quiet",
            "git diff --cached --quiet",
        ):
            self.assertIn(marker, HELPERS)

    def test_both_binaries_recovery_and_provenance_are_bound(self) -> None:
        combined = WORKFLOW + "\n" + HELPERS
        for marker in (
            "image-ref: ${{ env.CANDIDATE_IMAGE }}",
            "release-image-manifest.json",
            "sidecar-image-manifest.json",
            "registry-publish-receipt.json",
            "whatsapp-sidecar-registry-publish-receipt.json",
            "scripts/qualification/recovery/run_recovery_qualification.sh",
            "actions/attest-build-provenance@"
            "0f67c3f4856b2e3261c31976d6725780e5e4c373",
            "subject-digest: ${{ steps.identity.outputs.application_digest }}",
            "subject-digest: ${{ steps.identity.outputs.sidecar_digest }}",
            "push-to-registry: true",
            "create-storage-record: false",
        ):
            self.assertIn(marker, combined)
        self.assertEqual(
            WORKFLOW.count(
                "actions/attest-build-provenance@"
                "0f67c3f4856b2e3261c31976d6725780e5e4c373"
            ),
            2,
        )
        login = WORKFLOW.index("Authenticate GHCR for registry attestation")
        attest_application = WORKFLOW.index(
            "Attest exact application registry digest"
        )
        attest_sidecar = WORKFLOW.index(
            "Attest exact WhatsApp Sidecar registry digest"
        )
        logout = WORKFLOW.index("Clear GHCR registry credentials")
        finalize = WORKFLOW.index("Build final evidence-bound candidate")
        self.assertLess(login, attest_application)
        self.assertLess(attest_application, attest_sidecar)
        self.assertLess(attest_sidecar, logout)
        self.assertLess(logout, finalize)
        self.assertIn("docker logout ghcr.io", WORKFLOW)

    def test_final_artifact_binds_acceptance_and_both_server_images(self) -> None:
        for marker in (
            "nexus.canonical-acceptance-receipt.v1",
            "CANONICAL_ACCEPTANCE_RUN_ID",
            "CANONICAL_ACCEPTANCE_RUN_URL",
            "controlled-candidate.env",
            "CONTROLLED_IMAGE=${image}",
            "WHATSAPP_SIDECAR_IMAGE=${sidecar_image}",
            "GIT_SHA=${SOURCE_SHA}",
            "FRONTEND_BUILD_SHA=",
            "EXPECTED_MIGRATION_HEAD=",
            "ACTIVATION_EVIDENCE_SOURCE_SHA=${SOURCE_SHA}",
            "ACTIVATION_EVIDENCE_IMAGE_DIGEST=${digest}",
            'rm -f "$FINAL_DIR/artifact-scan.json"',
            "scan_controlled_candidate_artifacts.py",
        ):
            self.assertIn(marker, WORKFLOW)
        for marker in (
            "--sidecar-registry-image",
            "--sidecar-registry-digest",
            "--sidecar-local-image-id",
            "--sidecar-pulled-image-id",
            "--sidecar-attestation-id",
            "--sidecar-attestation-url",
            "whatsapp_sidecar_attestation",
        ):
            self.assertIn(marker, HELPERS)

    def test_controlled_candidate_remains_fail_closed_for_external_effects(
        self,
    ) -> None:
        for marker in (
            "PROVIDER_RUNTIME_KILL_SWITCH=true",
            "PROVIDER_RUNTIME_CANARY_PERCENT=0",
            "ENABLE_OUTBOUND_DISPATCH=false",
            "WHATSAPP_ENABLED=false",
            "WHATSAPP_EMBEDDED_SIGNUP_ENABLED=false",
            "WHATSAPP_MEDIA_ENABLED=false",
            "WHATSAPP_MEDIA_SCANNER=disabled",
            "SPEEDAF_WORK_ORDER_CREATE_ENABLED=false",
            "OPERATIONS_DISPATCH_MODE=disabled",
            "ALLOW_DEV_AUTH=false",
            "LOCAL_STORAGE_BACKUP_REQUIRED=true",
        ):
            self.assertIn(marker, ENV_EXAMPLE)
        self.assertNotIn("WHATSAPP_NATIVE_ENABLED", ENV_EXAMPLE)
        self.assertNotIn("WHATSAPP_DISPATCH_MODE", ENV_EXAMPLE)
        self.assertIn(
            "- Controlled deployment performed: `false`",
            WORKFLOW,
        )
        self.assertIn("- External effects authorized: `false`", WORKFLOW)

    def test_controlled_compose_is_digest_only_with_optional_canonical_sidecar(
        self,
    ) -> None:
        self.assertIn("${CONTROLLED_IMAGE:?", COMPOSE)
        self.assertNotIn("NEXUS_RUNTIME_SECRETS_HOST_PATH", COMPOSE)
        self.assertNotIn("env_file:", COMPOSE)
        self.assertNotRegex(COMPOSE, r"(?m)^\s*build\s*:")
        self.assertNotIn(":latest", COMPOSE)
        self.assertNotIn("external: true", COMPOSE)
        self.assertNotIn("production_runtime", COMPOSE)
        self.assertIn("whatsapp-sidecar-controlled:", COMPOSE)
        self.assertIn("${WHATSAPP_SIDECAR_IMAGE:?", COMPOSE)
        self.assertIn("- whatsapp-baileys", COMPOSE)
        for service in (
            "migrate-controlled:",
            "app-controlled:",
            "worker-outbound-controlled:",
            "worker-background-controlled:",
            "worker-webchat-ai-controlled:",
            "whatsapp-sidecar-controlled:",
        ):
            self.assertIn(service, COMPOSE)
        self.assertNotIn("worker-handoff-snapshot-controlled:", COMPOSE)

    def test_canonical_acceptance_treats_sidecar_as_first_class_supply_chain(
        self,
    ) -> None:
        for marker in (
            "sidecar-supply-chain:",
            "connectors/whatsapp-sidecar/package-lock.json",
            "connectors/whatsapp-sidecar/Dockerfile",
            "npm run typecheck",
            "npm test",
            "sidecar.raw.cdx.json",
            "sidecar-image.raw.cdx.json",
            "sidecar-trivy.raw.json",
            "SIDECAR_SUPPLY_CHAIN",
        ):
            self.assertIn(marker, CANONICAL)
        self.assertNotIn("WHATSAPP_NATIVE_ENABLED", CANONICAL)
        self.assertNotIn("WHATSAPP_DISPATCH_MODE", CANONICAL)


if __name__ == "__main__":
    unittest.main()
