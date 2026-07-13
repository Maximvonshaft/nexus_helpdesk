from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "validate_controlled_server_preflight.py"
SPEC = importlib.util.spec_from_file_location("validate_controlled_server_preflight", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ControlledServerPreflightTests(unittest.TestCase):
    source = "a" * 40
    digest = "sha256:" + "b" * 64
    image = "ghcr.io/maximvonshaft/nexus_helpdesk@" + digest

    def _write_fixture(self, root: Path) -> tuple[Path, Path, Path]:
        manifest = {
            "schema": "nexus.osr.controlled-candidate-manifest.v1",
            "status": "pass",
            "candidate": {
                "source_sha": self.source,
                "frontend_build_sha": self.source,
                "migration_revision": "20260713_0059",
                "registry_digest": self.digest,
                "registry_reference": self.image,
            },
            "safety": {
                "production_ready": False,
                "full_osr_automation": "NO_GO",
                "issue_533_go": False,
                "deployment_performed": False,
                "external_effects_authorized": False,
            },
        }
        manifest_path = root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        compose_path = root / "compose.yml"
        compose_path.write_text(
            """services:
  migrate-controlled:
    image: ${CONTROLLED_IMAGE:?digest required}
  app-controlled:
    image: ${CONTROLLED_IMAGE:?digest required}
  worker-outbound-controlled:
    image: ${CONTROLLED_IMAGE:?digest required}
  worker-background-controlled:
    image: ${CONTROLLED_IMAGE:?digest required}
  worker-webchat-ai-controlled:
    image: ${CONTROLLED_IMAGE:?digest required}
  worker-handoff-snapshot-controlled:
    image: ${CONTROLLED_IMAGE:?digest required}
""",
            encoding="utf-8",
        )
        values = {
            "CONTROLLED_IMAGE": self.image,
            "IMAGE_TAG": self.image,
            "GIT_SHA": self.source,
            "FRONTEND_BUILD_SHA": self.source,
            "EXPECTED_MIGRATION_HEAD": "20260713_0059",
            "APP_ENV": "production",
            "READINESS_REQUIRE_RELEASE_METADATA": "true",
            "DATABASE_URL": "postgresql+psycopg://user:secret@10.2.64.2:5432/nexusdesk",
            "ALLOWED_ORIGINS": "https://mcs.speedaf.com",
            "WEBCHAT_ALLOWED_ORIGINS": "https://mcs.speedaf.com",
            "NEXUS_UPLOADS_HOST_PATH": "/opt/nexus_helpdesk/data/uploads",
            "NEXUS_UPLOAD_BACKUP_HOST_PATH": "/var/backups/nexusdesk/uploads",
            "AI_RUNTIME_TOKEN_HOST_PATH": "/opt/nexus_helpdesk/deploy/runtime_secrets/ai_runtime_token",
            **MODULE.SAFE_CONTROLS,
        }
        env_path = root / ".env.controlled"
        env_path.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
        return env_path, compose_path, manifest_path

    def test_accepts_digest_only_safe_cutover(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env_path, compose_path, manifest_path = self._write_fixture(root)
            payload = MODULE.validate(
                env_path=env_path,
                compose_path=compose_path,
                manifest_path=manifest_path,
                expected_database_host="10.2.64.2",
                expected_domain="mcs.speedaf.com",
                check_host_paths=False,
            )
            self.assertEqual(payload["status"], "pass")
            self.assertFalse(payload["external_effects_enabled"])

    def test_rejects_mutable_image_tag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env_path, compose_path, manifest_path = self._write_fixture(root)
            text = env_path.read_text().replace(self.image, "ghcr.io/maximvonshaft/nexus_helpdesk:latest")
            env_path.write_text(text)
            with self.assertRaisesRegex(MODULE.PreflightError, "controlled_image_not_digest_pinned"):
                MODULE.validate(
                    env_path=env_path,
                    compose_path=compose_path,
                    manifest_path=manifest_path,
                    expected_database_host="10.2.64.2",
                    expected_domain="mcs.speedaf.com",
                    check_host_paths=False,
                )

    def test_rejects_reenabled_provider_canary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env_path, compose_path, manifest_path = self._write_fixture(root)
            text = env_path.read_text().replace("PROVIDER_RUNTIME_CANARY_PERCENT=0", "PROVIDER_RUNTIME_CANARY_PERCENT=100")
            env_path.write_text(text)
            with self.assertRaisesRegex(MODULE.PreflightError, "unsafe_control:PROVIDER_RUNTIME_CANARY_PERCENT"):
                MODULE.validate(
                    env_path=env_path,
                    compose_path=compose_path,
                    manifest_path=manifest_path,
                    expected_database_host="10.2.64.2",
                    expected_domain="mcs.speedaf.com",
                    check_host_paths=False,
                )

    def test_rejects_compose_build_directive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env_path, compose_path, manifest_path = self._write_fixture(root)
            compose_path.write_text(compose_path.read_text() + "    build: .\n")
            with self.assertRaisesRegex(MODULE.PreflightError, "compose_build_forbidden"):
                MODULE.validate(
                    env_path=env_path,
                    compose_path=compose_path,
                    manifest_path=manifest_path,
                    expected_database_host="10.2.64.2",
                    expected_domain="mcs.speedaf.com",
                    check_host_paths=False,
                )


if __name__ == "__main__":
    unittest.main()
