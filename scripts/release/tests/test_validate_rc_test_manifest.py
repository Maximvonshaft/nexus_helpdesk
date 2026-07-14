from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "validate_rc_test_manifest.py"
SPEC = importlib.util.spec_from_file_location("validate_rc_test_manifest", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

ROOT = Path(__file__).resolve().parents[3]


def _digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def valid_manifest(root: Path) -> tuple[dict, Path]:
    evidence = {}
    for index, logical_name in enumerate(MODULE.REQUIRED_EVIDENCE):
        filename = f"evidence-{index:02d}-{logical_name}.txt"
        path = root / filename
        path.write_text(f"bounded {logical_name} evidence\n", encoding="utf-8")
        evidence[logical_name] = {
            "path": filename,
            "size_bytes": path.stat().st_size,
            "sha256": _digest(path),
        }
    payload = {
        "schema": "nexus.osr.rc-test-candidate.v1",
        "release_class": "controlled_test_deployment",
        "decision": "RC0_TEST_DEPLOYABLE",
        "candidate": {
            "source_sha": "a" * 40,
            "frontend_build_sha": "a" * 40,
            "image_tag": "nexusdesk/helpdesk:rc-test-a",
            "image_id": "sha256:" + "b" * 64,
            "postgres_image_digest": "pgvector/pgvector@sha256:" + "c" * 64,
            "nginx_image_digest": "nginx@sha256:" + "d" * 64,
            "migration_revision": "20260711_0058",
            "config_profile": "rc-test-isolated-v1",
            "config_digest": "sha256:" + "e" * 64,
        },
        "checks": {name: "pass" for name in MODULE.REQUIRED_CHECKS},
        "safety": {
            "production_data_used": False,
            "production_network_joined": False,
            "provider_candidate_enabled": False,
            "real_outbound_enabled": False,
            "whatsapp_enabled": False,
            "speedaf_write_enabled": False,
            "operations_dispatch_enabled": False,
            "production_ready": False,
            "full_osr_automation": "NO_GO",
            "test_environment_isolated": True,
        },
        "evidence": evidence,
    }
    manifest_path = root / "candidate-manifest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    return payload, manifest_path


class ManifestValidationTests(unittest.TestCase):
    def test_accepts_complete_digest_bound_isolated_candidate_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload, manifest_path = valid_manifest(Path(tmp))
            MODULE.validate_manifest(payload, manifest_path)

    def test_rejects_unsafe_or_incomplete_manifest(self):
        cases = [
            (("decision",), "PRODUCTION_GO"),
            (("candidate", "source_sha"), "bad"),
            (("candidate", "frontend_build_sha"), "b" * 40),
            (("candidate", "image_id"), "sha256:short"),
            (("candidate", "migration_revision"), "head"),
            (("candidate", "postgres_image_digest"), "pgvector/pgvector:pg16"),
            (("checks", "browser_smoke"), "not_run"),
            (("checks", "network_isolation"), "not_run"),
            (("safety", "real_outbound_enabled"), True),
            (("safety", "operations_dispatch_enabled"), True),
            (("safety", "production_ready"), True),
            (("safety", "full_osr_automation"), "GO"),
            (("safety", "test_environment_isolated"), False),
        ]
        for path, value in cases:
            with self.subTest(path=path, value=value), tempfile.TemporaryDirectory() as tmp:
                payload, manifest_path = valid_manifest(Path(tmp))
                payload = copy.deepcopy(payload)
                cursor = payload
                for key in path[:-1]:
                    cursor = cursor[key]
                cursor[path[-1]] = value
                with self.assertRaises(MODULE.ManifestError):
                    MODULE.validate_manifest(payload, manifest_path)

    def test_rejects_missing_unexpected_or_reused_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload, manifest_path = valid_manifest(Path(tmp))
            missing = copy.deepcopy(payload)
            missing["evidence"].pop("health")
            with self.assertRaises(MODULE.ManifestError):
                MODULE.validate_manifest(missing, manifest_path)

            unexpected = copy.deepcopy(payload)
            unexpected["evidence"]["raw_logs"] = copy.deepcopy(unexpected["evidence"]["health"])
            with self.assertRaises(MODULE.ManifestError):
                MODULE.validate_manifest(unexpected, manifest_path)

            reused = copy.deepcopy(payload)
            reused["evidence"]["readiness"] = copy.deepcopy(reused["evidence"]["health"])
            with self.assertRaises(MODULE.ManifestError):
                MODULE.validate_manifest(reused, manifest_path)

    def test_rejects_traversal_backslash_and_digest_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload, manifest_path = valid_manifest(Path(tmp))
            for bad_path in ("../outside.txt", "nested/file.txt", "nested\\file.txt", "/tmp/file.txt"):
                candidate = copy.deepcopy(payload)
                candidate["evidence"]["health"]["path"] = bad_path
                with self.subTest(path=bad_path), self.assertRaises(MODULE.ManifestError):
                    MODULE.validate_manifest(candidate, manifest_path)

            bad_digest = copy.deepcopy(payload)
            bad_digest["evidence"]["health"]["sha256"] = "sha256:" + "f" * 64
            with self.assertRaises(MODULE.ManifestError):
                MODULE.validate_manifest(bad_digest, manifest_path)

    def test_load_rejects_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidate-manifest.json"
            path.write_text('{"schema":"a","schema":"b"}', encoding="utf-8")
            with self.assertRaises(MODULE.ManifestError):
                MODULE.load_manifest(path)


class TopologyAndWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = ROOT
        cls.compose = (ROOT / "deploy" / "docker-compose.rc-test.yml").read_text(encoding="utf-8")
        cls.env_example = (ROOT / "deploy" / ".env.rc-test.example").read_text(encoding="utf-8")
        cls.runner = (ROOT / "scripts" / "release" / "run_rc_test_candidate.sh").read_text(encoding="utf-8")
        cls.workflow = (ROOT / ".github" / "workflows" / "rc-test-candidate.yml").read_text(encoding="utf-8")
        cls.seed = (ROOT / "scripts" / "release" / "seed_rc_test_data.py").read_text(encoding="utf-8")
        cls.dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        cls.playwright = (ROOT / "webapp" / "playwright.config.ts").read_text(encoding="utf-8")
        cls.browser = (ROOT / "webapp" / "e2e" / "rc-live.spec.ts").read_text(encoding="utf-8")

    def test_single_canonical_application_image_owns_frontend_dist(self):
        self.assertIn("x-rc-app: &rc_app", self.compose)
        self.assertIn("image: ${RC_IMAGE_TAG:", self.compose)
        for service in (
            "app-rc", "migrate-rc", "seed-rc", "worker-outbound-rc", "worker-background-rc",
            "worker-webchat-ai-rc", "worker-handoff-snapshot-rc",
        ):
            self.assertRegex(
                self.compose,
                rf"(?ms)^  {re.escape(service)}:\n.*?^    <<: \*rc_app$",
            )
        self.assertIn("COPY --from=webapp-builder /build/frontend_dist /app/frontend_dist", self.dockerfile)
        self.assertNotIn("RC_FRONTEND_IMAGE", self.compose + self.env_example + self.runner)
        self.assertNotIn("frontend-rc:", self.compose)
        self.assertNotIn("sync-daemon-rc:", self.compose)
        self.assertNotIn("event-daemon-rc:", self.compose)

    def test_postgres_receives_only_database_environment(self):
        block = self.compose.split("  postgres-rc:\n", 1)[1].split("\n  migrate-rc:\n", 1)[0]
        self.assertNotIn("env_file:", block)
        for key in ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"):
            self.assertIn(key, block)
        for forbidden in ("SECRET_KEY", "RC_TEST_ADMIN_PASSWORD", "RUNTIME_CONTRACT_SIGNING_SECRET"):
            self.assertNotIn(forbidden, block)

    def test_rc_profile_is_fail_closed_and_contains_no_live_credentials(self):
        for token in (
            "APP_ENV=production",
            "ALLOW_DEV_AUTH=false",
            "TENANT_RUNTIME_AUTHORITY_MODE=enforce",
            "EXTERNAL_CHANNEL_TRANSPORT=disabled",
            "EXTERNAL_CHANNEL_DEPLOYMENT_MODE=disabled",
            "EXTERNAL_CHANNEL_CLI_FALLBACK_ENABLED=false",
            "PROVIDER_RUNTIME_KILL_SWITCH=true",
            "ENABLE_OUTBOUND_DISPATCH=false",
            "WHATSAPP_NATIVE_ENABLED=false",
            "SPEEDAF_WORK_ORDER_CREATE_ENABLED=false",
            "OPERATIONS_DISPATCH_MODE=disabled",
        ):
            self.assertIn(token, self.env_example)
        self.assertNotIn("OPENAI_API_KEY", self.env_example)
        self.assertNotIn("PROVIDER_RUNTIME_LIVE_PROBE_TOKEN", self.env_example)

    def test_exact_source_and_image_identity_are_fail_closed(self):
        self.assertIn("RC_SOURCE_SHA=<40-char-git-sha>", self.env_example)
        self.assertIn('SOURCE_SHA="${GIT_SHA}"', self.runner)
        self.assertIn('RC_SOURCE_SHA does not match GIT_SHA', self.runner)
        self.assertIn('IMAGE_TAG_VALUE="${RC_IMAGE_TAG}"', self.runner)
        self.assertIn('docker image inspect "${IMAGE_TAG_VALUE}"', self.runner)
        self.assertIn("LABEL org.opencontainers.image.revision=${GIT_SHA}", self.dockerfile)
        self.assertIn('ref: ${{ github.event.pull_request.head.sha || github.sha }}', self.workflow)
        self.assertIn('RC_SOURCE_SHA: ${{ github.event.pull_request.head.sha || github.sha }}', self.workflow)

    def test_runner_uses_explicit_isolated_compose_identity_and_cleanup(self):
        self.assertIn("name: ${COMPOSE_PROJECT_NAME:-nexus_rc_test}", self.compose)
        self.assertIn('PROJECT_NAME="${COMPOSE_PROJECT_NAME:-nexus_rc_test}"', self.runner)
        self.assertIn('export COMPOSE_PROJECT_NAME="${PROJECT_NAME}"', self.runner)
        self.assertIn('docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}"', self.runner)
        self.assertIn("down --volumes --remove-orphans", self.runner)
        self.assertIn("trap cleanup_stack EXIT", self.runner)

    def test_app_healthcheck_and_loopback_gateway_are_runtime_compatible(self):
        app_block = self.compose.split("  app-rc:\n", 1)[1].split("\n  worker-outbound-rc:\n", 1)[0]
        self.assertNotIn("- curl\n", app_block)
        self.assertIn("urllib.request.urlopen('http://127.0.0.1:8080/readyz', timeout=4).read()", app_block)
        self.assertNotIn("    ports:\n", app_block)
        nginx_block = self.compose.split("  nginx-rc:\n", 1)[1].split("\nnetworks:\n", 1)[0]
        self.assertIn('127.0.0.1:${RC_APP_PORT:-18083}:80', nginx_block)
        self.assertIn("      - rc\n      - edge", nginx_block)

    def test_seed_and_runner_bind_to_relational_tenant_authority(self):
        self.assertIn("register_all_models()", self.seed)
        self.assertIn('"RC_PUBLIC_ORIGIN"', self.seed)
        self.assertIn("normalize_public_origin(requested_origin)", self.seed)
        self.assertIn("service_completed_successfully", self.compose)
        self.assertIn("Tenant.tenant_key == tenant_key", self.runner)
        self.assertIn("user.tenant_id = tenant.id", self.runner)
        self.assertIn("tenant_assignment_source", self.runner)
        self.assertIn("tenant_assignment_version", self.runner)
        self.assertIn("TENANT_RUNTIME_AUTHORITY_MODE", self.runner)

    def test_runner_proves_exact_migration_browser_and_all_failure_logs(self):
        self.assertIn("RC requires exactly one Alembic head", self.runner)
        self.assertIn("MIGRATION_CURRENT", self.runner)
        self.assertIn("e2e/rc-live.spec.ts", self.runner)
        self.assertIn("rc_test_side_effects.py", self.runner)
        for service in (
            "postgres-rc", "migrate-rc", "seed-rc", "app-rc", "nginx-rc",
            "worker-outbound-rc", "worker-background-rc", "worker-webchat-ai-rc",
            "worker-handoff-snapshot-rc",
        ):
            self.assertIn(service, self.runner)

    def test_browser_binds_operator_thread_to_server_conversation_identity(self):
        identity_extract = "new URL(messageResponse.url()).pathname.match"
        session_key = "const operatorSessionKey = `webchat:${conversationId}`"
        operator_path = "`/webchat?session=${encodeURIComponent(operatorSessionKey)}`"
        body_selector = "page.locator('.operator-message p', { hasText: message }).first()"
        self.assertIn(identity_extract, self.browser)
        self.assertIn("^\\/api\\/webchat\\/conversations\\/(wc_[A-Za-z0-9_-]+)\\/messages$", self.browser)
        self.assertIn(session_key, self.browser)
        self.assertIn(operator_path, self.browser)
        self.assertIn(body_selector, self.browser)
        self.assertLess(self.browser.index(identity_extract), self.browser.index(session_key))
        self.assertLess(self.browser.index(session_key), self.browser.index(operator_path))
        self.assertLess(self.browser.index(operator_path), self.browser.index(body_selector))
        self.assertNotIn("button.support-row', { hasText: message }", self.browser)
        self.assertNotIn("await matchingRow.click()", self.browser)

    def test_rc_browser_stage_is_bound_without_stateful_retries(self):
        self.assertIn("RC_BROWSER_STAGE_FILE", self.browser)
        self.assertIn("writeFileSync(browserStageFile", self.browser)
        self.assertIn('browser_stage_file="${RUNNER_TEMP}/rc-browser-stage"', self.workflow)
        self.assertIn('RC_BROWSER_STAGE_FILE="${browser_stage_file}"', self.workflow)
        self.assertIn("tr -d", self.workflow)
        self.assertIn("retries: rcBrowser ? 0", self.playwright)
        self.assertNotIn("artifacts/rc-test/browser-stage", self.workflow)

    def test_workflow_runs_bounded_preflight_and_scans_explicit_evidence(self):
        self.assertIn("id: rc-preflight", self.workflow)
        self.assertIn("python scripts/release/rc_preflight.py --artifact-root artifacts/rc-test", self.workflow)
        self.assertIn("validate_rc_test_evidence.py", self.workflow)
        self.assertIn('"${evidence_files[@]}"', self.workflow)
        self.assertIn("steps.scan-evidence.outcome == 'success'", self.workflow)
        self.assertNotIn("artifacts/rc-test\n", self.workflow.split("scan_artifacts.py", 1)[1].split("Upload", 1)[0])


if __name__ == "__main__":
    unittest.main()
