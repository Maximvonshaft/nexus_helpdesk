from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "scripts" / "release" / "validate_rc_test_manifest.py"
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
    def test_accepts_complete_digest_bound_isolated_candidate_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload, manifest_path = valid_manifest(Path(tmp))
            MODULE.validate_manifest(payload, manifest_path)

    def test_rejects_unsafe_or_incomplete_manifest(self) -> None:
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
                candidate = copy.deepcopy(payload)
                cursor = candidate
                for key in path[:-1]:
                    cursor = cursor[key]
                cursor[path[-1]] = value
                with self.assertRaises(MODULE.ManifestError):
                    MODULE.validate_manifest(candidate, manifest_path)

    def test_rejects_missing_unexpected_reused_or_escaping_evidence(self) -> None:
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

            for bad_path in ("../outside.txt", "nested/file.txt", "nested\\file.txt", "/tmp/file.txt"):
                escaping = copy.deepcopy(payload)
                escaping["evidence"]["health"]["path"] = bad_path
                with self.subTest(path=bad_path), self.assertRaises(MODULE.ManifestError):
                    MODULE.validate_manifest(escaping, manifest_path)

            bad_digest = copy.deepcopy(payload)
            bad_digest["evidence"]["health"]["sha256"] = "sha256:" + "f" * 64
            with self.assertRaises(MODULE.ManifestError):
                MODULE.validate_manifest(bad_digest, manifest_path)

    def test_load_rejects_duplicate_json_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidate-manifest.json"
            path.write_text('{"schema":"a","schema":"b"}', encoding="utf-8")
            with self.assertRaises(MODULE.ManifestError):
                MODULE.load_manifest(path)


class CurrentRcTopologyAndAuthorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.compose = (ROOT / "deploy/docker-compose.rc-test.yml").read_text(encoding="utf-8")
        cls.env_example = (ROOT / "deploy/.env.rc-test.example").read_text(encoding="utf-8")
        cls.runner = (ROOT / "scripts/release/run_rc_test_candidate.sh").read_text(encoding="utf-8")
        cls.gate = (ROOT / "scripts/release/run_controlled_rc_gate.sh").read_text(encoding="utf-8")
        cls.workflow = (ROOT / ".github/workflows/canonical-acceptance.yml").read_text(encoding="utf-8")
        cls.seed = (ROOT / "scripts/release/seed_rc_test_data.py").read_text(encoding="utf-8")
        cls.dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        cls.playwright = (ROOT / "webapp/playwright.config.ts").read_text(encoding="utf-8")
        cls.browser = (ROOT / "webapp/e2e/rc-live.spec.ts").read_text(encoding="utf-8")

    def test_single_canonical_application_image_owns_frontend_dist(self) -> None:
        self.assertIn("x-rc-app: &rc_app", self.compose)
        self.assertIn("image: ${RC_IMAGE_TAG:", self.compose)
        for service in (
            "app-rc", "migrate-rc", "seed-rc", "worker-outbound-rc",
            "worker-background-rc", "worker-webchat-ai-rc", "worker-handoff-snapshot-rc",
        ):
            self.assertIn(f"  {service}:\n", self.compose)
            block = self.compose.split(f"  {service}:\n", 1)[1].split("\n  ", 1)[0]
            self.assertIn("<<: *rc_app", block)
        self.assertIn("COPY --from=webapp-builder /build/frontend_dist /app/frontend_dist", self.dockerfile)
        self.assertNotIn("RC_FRONTEND_IMAGE", self.compose + self.env_example + self.runner)
        self.assertNotIn("frontend-rc:", self.compose)

    def test_postgres_receives_only_database_environment(self) -> None:
        block = self.compose.split("  postgres-rc:\n", 1)[1].split("\n  migrate-rc:\n", 1)[0]
        self.assertNotIn("env_file:", block)
        for key in ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"):
            self.assertIn(key, block)
        for forbidden in ("SECRET_KEY", "RC_TEST_ADMIN_PASSWORD", "RUNTIME_CONTRACT_SIGNING_SECRET"):
            self.assertNotIn(forbidden, block)

    def test_rc_profile_is_fail_closed_and_contains_no_live_credentials(self) -> None:
        for token in (
            "APP_ENV=production", "ALLOW_DEV_AUTH=false", "TENANT_RUNTIME_AUTHORITY_MODE=enforce",
            "PROVIDER_RUNTIME_KILL_SWITCH=true", "PROVIDER_RUNTIME_CANARY_PERCENT=0",
            "ENABLE_OUTBOUND_DISPATCH=false", "WHATSAPP_NATIVE_ENABLED=false",
            "SPEEDAF_WORK_ORDER_CREATE_ENABLED=false", "OPERATIONS_DISPATCH_MODE=disabled",
        ):
            self.assertIn(token, self.env_example)
        self.assertNotIn("OPENAI_API_KEY", self.env_example)
        self.assertNotIn("PROVIDER_RUNTIME_LIVE_PROBE_TOKEN", self.env_example)

    def test_exact_source_image_and_main_only_publication_are_fail_closed(self) -> None:
        self.assertIn("RC_SOURCE_SHA=<40-char-git-sha>", self.env_example)
        self.assertIn('SOURCE_SHA="${GIT_SHA}"', self.runner)
        self.assertIn("RC_SOURCE_SHA does not match GIT_SHA", self.runner)
        self.assertIn('IMAGE_TAG_VALUE="${RC_IMAGE_TAG}"', self.runner)
        self.assertIn('docker image inspect "${IMAGE_TAG_VALUE}"', self.runner)
        self.assertIn("LABEL org.opencontainers.image.revision=${GIT_SHA}", self.dockerfile)
        self.assertIn("controlled-build-assure-publish:", self.workflow)
        self.assertIn("github.ref == 'refs/heads/main'", self.workflow)
        self.assertIn("needs.required-gate.result == 'success'", self.workflow)
        self.assertIn("RC_SOURCE_SHA: ${{ needs.candidate-identity.outputs.source_sha }}", self.workflow)
        self.assertIn("scripts/release/run_controlled_rc_gate.sh", self.workflow)

    def test_runner_uses_explicit_isolated_compose_identity_and_cleanup(self) -> None:
        self.assertIn("name: ${COMPOSE_PROJECT_NAME:-nexus_rc_test}", self.compose)
        self.assertIn('PROJECT_NAME="${COMPOSE_PROJECT_NAME:-nexus_rc_test}"', self.runner)
        self.assertIn('export COMPOSE_PROJECT_NAME="${PROJECT_NAME}"', self.runner)
        self.assertIn('docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}"', self.runner)
        self.assertIn("down --volumes --remove-orphans", self.runner)
        self.assertIn("trap cleanup_stack EXIT", self.runner)

    def test_browser_and_operator_identity_are_bound_to_server_conversation(self) -> None:
        self.assertIn("new URL(messageResponse.url()).pathname.match", self.browser)
        self.assertIn("const operatorSessionKey = `webchat:${conversationId}`", self.browser)
        self.assertIn("`/webchat?session=${encodeURIComponent(operatorSessionKey)}`", self.browser)
        self.assertIn("page.locator('.operator-message p', { hasText: message }).first()", self.browser)
        self.assertIn("RC_BROWSER_STAGE_FILE", self.browser)
        self.assertIn("writeFileSync(browserStageFile", self.browser)
        self.assertIn("retries: rcBrowser ? 0", self.playwright)

    def test_gate_validates_manifest_and_scans_only_explicit_evidence(self) -> None:
        self.assertIn("python -m unittest discover -s scripts/release/tests", self.gate)
        self.assertIn("validate_rc_test_evidence.py", self.gate)
        self.assertIn('mapfile -t evidence_files < "${list_file}"', self.gate)
        self.assertIn('"${evidence_files[@]}"', self.gate)
        self.assertIn("scan_artifacts.py", self.gate)
        self.assertIn("RC0_TEST_DEPLOYABLE", self.gate)
        self.assertIn("candidate.source_sha", self.gate)

    def test_runner_proves_migration_workers_seed_and_all_failure_logs(self) -> None:
        self.assertIn("RC requires exactly one Alembic head", self.runner)
        self.assertIn("MIGRATION_CURRENT", self.runner)
        self.assertIn("e2e/rc-live.spec.ts", self.runner)
        self.assertIn("rc_test_side_effects.py", self.runner)
        self.assertIn("Tenant.tenant_key == tenant_key", self.runner)
        self.assertIn("register_all_models()", self.seed)
        for service in (
            "postgres-rc", "migrate-rc", "seed-rc", "app-rc", "nginx-rc",
            "worker-outbound-rc", "worker-background-rc", "worker-webchat-ai-rc",
            "worker-handoff-snapshot-rc",
        ):
            self.assertIn(service, self.runner)


if __name__ == "__main__":
    unittest.main()
