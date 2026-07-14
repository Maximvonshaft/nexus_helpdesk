from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "validate_rc_test_manifest.py"
SPEC = importlib.util.spec_from_file_location("validate_rc_test_manifest", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


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
        cls.root = Path(__file__).resolve().parents[3]
        cls.compose = (cls.root / "deploy" / "docker-compose.rc-test.yml").read_text(encoding="utf-8")
        cls.runner = (cls.root / "scripts" / "release" / "run_rc_test_candidate.sh").read_text(encoding="utf-8")
        cls.workflow = (cls.root / ".github" / "workflows" / "rc-test-candidate.yml").read_text(encoding="utf-8")
        cls.seed = (cls.root / "scripts" / "release" / "seed_rc_test_data.py").read_text(encoding="utf-8")
        cls.browser = (cls.root / "webapp" / "e2e" / "rc-live.spec.ts").read_text(encoding="utf-8")
        cls.playwright = (cls.root / "webapp" / "playwright.config.ts").read_text(encoding="utf-8")

    def test_postgres_receives_only_database_environment(self):
        block = self.compose.split("  postgres-rc:\n", 1)[1].split("\n  migrate-rc:\n", 1)[0]
        self.assertNotIn("env_file:", block)
        for key in ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"):
            self.assertIn(key, block)
        for forbidden in ("SECRET_KEY", "RC_TEST_ADMIN_PASSWORD", "RUNTIME_CONTRACT_SIGNING_SECRET"):
            self.assertNotIn(forbidden, block)

    def test_app_healthcheck_and_loopback_gateway_are_runtime_compatible(self):
        app_block = self.compose.split("  app-rc:\n", 1)[1].split("\n  worker-outbound-rc:\n", 1)[0]
        self.assertNotIn("- curl\n", app_block)
        self.assertIn("urllib.request.urlopen('http://127.0.0.1:8080/readyz', timeout=4).read()", app_block)
        self.assertNotIn("    ports:\n", app_block)
        nginx_block = self.compose.split("  nginx-rc:\n", 1)[1].split("\nnetworks:\n", 1)[0]
        self.assertIn('127.0.0.1:${RC_APP_PORT:-18083}:80', nginx_block)
        self.assertIn("      - rc\n      - edge", nginx_block)

    def test_seed_registers_models_and_uses_real_runtime_origin(self):
        self.assertIn("register_all_models()", self.seed)
        self.assertIn('"RC_PUBLIC_ORIGIN"', self.seed)
        self.assertIn("normalize_public_origin(requested_origin)", self.seed)
        self.assertIn("service_completed_successfully", self.compose)

    def test_runner_binds_synthetic_operator_to_seeded_tenant(self):
        self.assertIn("Tenant.tenant_key == tenant_key", self.runner)
        self.assertIn("user.tenant_id = tenant.id", self.runner)
        self.assertIn("tenant_assignment_source", self.runner)
        self.assertIn("tenant_assignment_version", self.runner)
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

    def test_browser_proves_public_message_and_single_customer_service_workspace(self):
        self.assertIn("const message = `RC browser synthetic message", self.browser)
        self.assertIn("/api/webchat/conversations/", self.browser)
        self.assertIn("const operatorResponse = await navigate(page, '/workspace')", self.browser)
        self.assertIn("level: 1, name: '客服工作台'", self.browser)
        self.assertIn("name: '主导航'", self.browser)
        self.assertIn("not.toContainText", self.browser)
        self.assertNotIn("/webchat?session=", self.browser)
        self.assertNotIn(".support-message-body", self.browser)
        self.assertLess(
            self.browser.index("messageResponse.ok()"),
            self.browser.index("const operatorResponse = await navigate(page, '/workspace')"),
        )

    def test_rc_browser_stage_is_bound_without_stateful_retries(self):
        self.assertIn("RC_BROWSER_STAGE_FILE", self.browser)
        self.assertIn("writeFileSync(browserStageFile", self.browser)
        self.assertIn('browser_stage_file="${RUNNER_TEMP}/rc-browser-stage"', self.workflow)
        self.assertIn('RC_BROWSER_STAGE_FILE="${browser_stage_file}"', self.workflow)
        self.assertIn("tr -d", self.workflow)
        self.assertIn("retries: rcBrowser ? 0", self.playwright)
        self.assertNotIn("artifacts/rc-test/browser-stage", self.workflow)

    def test_workflow_scans_explicit_files_and_uploads_only_after_success(self):
        self.assertIn("validate_rc_test_evidence.py", self.workflow)
        self.assertIn('"${evidence_files[@]}"', self.workflow)
        self.assertIn("steps.scan-evidence.outcome == 'success'", self.workflow)
        self.assertNotIn("artifacts/rc-test\n", self.workflow.split("scan_artifacts.py", 1)[1].split("Upload", 1)[0])


if __name__ == "__main__":
    unittest.main()
