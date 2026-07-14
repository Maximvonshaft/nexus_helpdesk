from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


class TopologyAndWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = ROOT
        cls.compose = (cls.root / "deploy" / "docker-compose.rc-test.yml").read_text(encoding="utf-8")
        cls.env_example = (cls.root / "deploy" / ".env.rc-test.example").read_text(encoding="utf-8")
        cls.runner = (cls.root / "scripts" / "release" / "run_rc_test_candidate.sh").read_text(encoding="utf-8")
        cls.workflow = (cls.root / ".github" / "workflows" / "rc-test-candidate.yml").read_text(encoding="utf-8")
        cls.playwright = (cls.root / "webapp" / "playwright.config.ts").read_text(encoding="utf-8")
        cls.browser = (cls.root / "webapp" / "e2e" / "rc-live.spec.ts").read_text(encoding="utf-8")

    def test_compose_has_single_immutable_application_build(self):
        build_count = len(re.findall(r"(?m)^\s+build:\s*$", self.compose))
        self.assertEqual(build_count, 1)
        self.assertIn("app-rc:", self.compose)
        self.assertIn("image: ${RC_IMAGE_TAG:?RC_IMAGE_TAG is required}", self.compose)
        self.assertIn("migrate-rc:", self.compose)
        self.assertIn("seed-rc:", self.compose)
        self.assertIn("worker-outbound-rc:", self.compose)
        self.assertIn("worker-background-rc:", self.compose)
        self.assertIn("worker-webchat-ai-rc:", self.compose)
        self.assertIn("worker-handoff-snapshot-rc:", self.compose)
        self.assertIn("nginx-rc:", self.compose)
        self.assertNotIn("sync-daemon-rc:", self.compose)
        self.assertNotIn("event-daemon-rc:", self.compose)

    def test_all_application_services_use_same_image(self):
        for service in (
            "app-rc", "migrate-rc", "seed-rc", "worker-outbound-rc", "worker-background-rc",
            "worker-webchat-ai-rc", "worker-handoff-snapshot-rc",
        ):
            pattern = rf"(?ms)^  {re.escape(service)}:.*?^    image: \$\{{RC_IMAGE_TAG:\?RC_IMAGE_TAG is required\}}$"
            self.assertRegex(self.compose, pattern)

    def test_compose_fail_closed_effect_switches(self):
        for token in (
            "EXTERNAL_CHANNEL_TRANSPORT: disabled",
            "EXTERNAL_CHANNEL_DEPLOYMENT_MODE: disabled",
            'EXTERNAL_CHANNEL_CLI_FALLBACK_ENABLED: "false"',
            'SPEEDAF_ENABLED: "false"',
            'SPEEDAF_TOOLS_ENABLED: "false"',
            'WEBCALL_AI_ENABLED: "false"',
            'WHATSAPP_NATIVE_ENABLED: "false"',
            'WHATSAPP_NATIVE_SEND_ENABLED: "false"',
            'OPERATIONS_DISPATCH_CONSUMER_ENABLED: "false"',
        ):
            self.assertIn(token, self.compose)

    def test_compose_uses_external_frontend_image_and_exact_source_label(self):
        self.assertIn("image: ${RC_FRONTEND_IMAGE:?RC_FRONTEND_IMAGE is required}", self.compose)
        self.assertIn("org.opencontainers.image.revision=${RC_SOURCE_SHA:?RC_SOURCE_SHA is required}", self.compose)
        self.assertIn("FRONTEND_BUILD_SHA: ${RC_SOURCE_SHA:?RC_SOURCE_SHA is required}", self.compose)

    def test_nginx_is_only_published_service(self):
        services = re.split(r"(?m)^  (?=[A-Za-z0-9_-]+:\s*$)", self.compose)
        published = []
        for block in services:
            match = re.match(r"([A-Za-z0-9_-]+):", block)
            if match and re.search(r"(?m)^    ports:\s*$", block):
                published.append(match.group(1))
        self.assertEqual(published, ["nginx-rc"])

    def test_compose_and_env_use_bounded_rc_only_values(self):
        for token in (
            "APP_ENV: production",
            "ALLOW_DEV_AUTH: \"false\"",
            "WEBCHAT_ALLOWED_ORIGINS: ${RC_PUBLIC_ORIGIN:?RC_PUBLIC_ORIGIN is required}",
            "DATABASE_URL: ${RC_DATABASE_URL:?RC_DATABASE_URL is required}",
            "PUBLIC_BASE_URL: ${RC_PUBLIC_ORIGIN:?RC_PUBLIC_ORIGIN is required}",
        ):
            self.assertIn(token, self.compose)
        self.assertIn("RC_SOURCE_SHA=", self.env_example)
        self.assertIn("RC_IMAGE_TAG=", self.env_example)
        self.assertIn("RC_FRONTEND_IMAGE=", self.env_example)
        self.assertIn("RC_PUBLIC_ORIGIN=", self.env_example)
        self.assertNotIn("OPENAI_API_KEY", self.env_example)
        self.assertNotIn("PROVIDER_RUNTIME_LIVE_PROBE_TOKEN", self.env_example)

    def test_runner_builds_exact_source_and_pins_image_identity(self):
        self.assertIn('test "$(git rev-parse HEAD)" = "${RC_SOURCE_SHA}"', self.runner)
        self.assertIn('export RC_IMAGE_TAG="nexusdesk/helpdesk:rc-test-${RC_SOURCE_SHA}"', self.runner)
        self.assertIn('export RC_FRONTEND_IMAGE="nexusdesk/frontend:rc-test-${RC_SOURCE_SHA}"', self.runner)
        self.assertIn('docker image inspect "${RC_IMAGE_TAG}"', self.runner)
        self.assertIn('org.opencontainers.image.revision', self.runner)

    def test_runner_isolated_compose_and_cleanup(self):
        self.assertIn('--project-name "${COMPOSE_PROJECT_NAME}"', self.runner)
        self.assertIn('--env-file "${RC_ENV_FILE}"', self.runner)
        self.assertIn('down --volumes --remove-orphans', self.runner)
        self.assertIn("trap cleanup EXIT", self.runner)

    def test_runner_uses_fail_closed_tenant_authority(self):
        self.assertIn("TENANT_RUNTIME_AUTHORITY_MODE", self.runner)
        self.assertIn("TENANT_RUNTIME_AUTHORITY_MODE=enforce", (self.root / "deploy" / ".env.rc-test.example").read_text(encoding="utf-8"))

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
        self.assertIn('tr -d', self.workflow)
        self.assertIn("retries: rcBrowser ? 0", self.playwright)
        self.assertNotIn("artifacts/rc-test/browser-stage", self.workflow)

    def test_workflow_scans_explicit_files_and_uploads_only_after_success(self):
        self.assertIn("validate_rc_test_evidence.py", self.workflow)
        self.assertIn('"${evidence_files[@]}"', self.workflow)
        self.assertIn("steps.scan-evidence.outcome == 'success'", self.workflow)
        self.assertNotIn("artifacts/rc-test\n", self.workflow.split("scan_artifacts.py", 1)[1].split("Upload", 1)[0])


if __name__ == "__main__":
    unittest.main()
