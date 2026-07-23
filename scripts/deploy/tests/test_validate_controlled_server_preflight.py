from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import yaml


MODULE_PATH = Path(__file__).resolve().parents[1] / "validate_controlled_server_preflight.py"
SPEC = importlib.util.spec_from_file_location(
    "validate_controlled_server_preflight",
    MODULE_PATH,
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ControlledServerPreflightTests(unittest.TestCase):
    source = "a" * 40
    image_id = "sha256:" + "9" * 64
    digest = "sha256:" + "b" * 64
    image = "ghcr.io/maximvonshaft/nexus_helpdesk@" + digest
    build_time = "20260713T190000Z"
    app_version = "controlled-aaaaaaaaaaaa"

    def _manifest(self) -> dict:
        return {
            "schema": "nexus.osr.controlled-candidate-manifest.v1",
            "status": "pass",
            "decision": "CONTROLLED_SERVER_CANDIDATE_PUBLISHED",
            "release_class": "controlled_server_deployment",
            "candidate": {
                "source_sha": self.source,
                "frontend_build_sha": self.source,
                "migration_revision": "20260713_0059",
                "local_image_id": self.image_id,
                "registry_pull_image_id": self.image_id,
                "registry_digest": self.digest,
                "registry_reference": self.image,
                "build_time": self.build_time,
                "app_version": self.app_version,
                "config_digest": "sha256:" + "c" * 64,
                "postgres_image_digest": "pgvector/pgvector@sha256:" + "d" * 64,
                "nginx_image_digest": "nginx@sha256:" + "e" * 64,
            },
            "attestation": {
                "id": "attestation-123",
                "url": "https://github.com/Maximvonshaft/nexus_helpdesk/attestations/123",
                "registry_provenance_pushed": True,
            },
            "safety": {
                "production_ready": False,
                "full_osr_automation": "NO_GO",
                "issue_533_go": False,
                "deployment_performed": False,
                "external_effects_authorized": False,
                "provider_enabled": False,
                "real_outbound_enabled": False,
                "whatsapp_enabled": False,
                "speedaf_writes_enabled": False,
                "operations_dispatch_enabled": False,
            },
        }

    @staticmethod
    def _service(*, environment: dict[str, str] | None = None, command: list[str]) -> dict:
        return {
            "image": "${CONTROLLED_IMAGE:?digest required}",
            "read_only": True,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "pids_limit": 256,
            "tmpfs": ["/tmp:rw,noexec,nosuid,size=64m"],
            "environment": environment or {},
            "command": command,
        }

    def _compose(self) -> str:
        services = {
            "migrate-controlled": self._service(
                environment={"DATABASE_URL": "${DATABASE_URL_MIGRATION:?required}"},
                command=["python", "-m", "alembic", "upgrade", "head"],
            ),
            "app-controlled": self._service(
                environment={"DATABASE_URL": "${DATABASE_URL_APP:?required}"},
                command=["python", "-m", "gunicorn", "app.main:app"],
            ),
            "livekit-agent-controlled": self._service(
                environment={
                    "LIVEKIT_API_KEY": "${LIVEKIT_API_KEY:-}",
                    "LIVEKIT_API_SECRET": "${LIVEKIT_API_SECRET:-}",
                    "LIVEKIT_API_KEY_FILE": "${LIVEKIT_API_KEY_FILE:-}",
                    "LIVEKIT_API_SECRET_FILE": "${LIVEKIT_API_SECRET_FILE:-}",
                    "LIVEKIT_AGENT_SHARED_SECRET": "${LIVEKIT_AGENT_SHARED_SECRET:-}",
                    "LIVEKIT_AGENT_SHARED_SECRET_FILE": "${LIVEKIT_AGENT_SHARED_SECRET_FILE:-}",
                },
                command=["python", "-m", "app.livekit_agent_worker", "start"],
            ),
            "worker-outbound-controlled": self._service(
                environment={"DATABASE_URL": "${DATABASE_URL_OUTBOUND:?required}"},
                command=["python", "scripts/run_worker.py", "--queue", "outbound"],
            ),
            "worker-background-controlled": self._service(
                environment={"DATABASE_URL": "${DATABASE_URL_BACKGROUND:?required}"},
                command=["python", "scripts/run_worker.py", "--queue", "background"],
            ),
            "worker-webchat-ai-controlled": self._service(
                environment={"DATABASE_URL": "${DATABASE_URL_WEBCHAT_AI:?required}"},
                command=["python", "scripts/run_worker.py", "--queue", "webchat-ai"],
            ),
            "worker-handoff-snapshot-controlled": self._service(
                environment={"DATABASE_URL": "${DATABASE_URL_HANDOFF:?required}"},
                command=["python", "scripts/run_worker.py", "--queue", "handoff-snapshot"],
            ),
        }
        return yaml.safe_dump(
            {"services": services, "networks": {"controlled": {"driver": "bridge"}}},
            sort_keys=False,
        )

    def _values(self) -> dict[str, str]:
        values = {
            "COMPOSE_PROJECT_NAME": "nexusdesk_controlled",
            "CONTROLLED_IMAGE": self.image,
            "IMAGE_TAG": self.image,
            "GIT_SHA": self.source,
            "FRONTEND_BUILD_SHA": self.source,
            "EXPECTED_MIGRATION_HEAD": "20260713_0059",
            "BUILD_TIME": self.build_time,
            "APP_VERSION": self.app_version,
            "CONTROLLED_APP_PORT": "18095",
            "APP_ENV": "production",
            "READINESS_REQUIRE_RELEASE_METADATA": "true",
            "SECRET_KEY": "s" * 48,
            "RUNTIME_CONTRACT_SIGNING_SECRET": "r" * 48,
            "METRICS_TOKEN": "m" * 48,
            "ALLOWED_ORIGINS": "https://mcs.speedaf.com",
            "WEBCHAT_ALLOWED_ORIGINS": "https://mcs.speedaf.com",
            "NEXUS_UPLOADS_HOST_PATH": "/opt/nexus_helpdesk/data/uploads",
            "NEXUS_UPLOAD_BACKUP_HOST_PATH": "/var/backups/nexusdesk/uploads",
            **MODULE.SAFE_CONTROLS,
        }
        for index, key in enumerate(MODULE.DATABASE_ROLE_KEYS.values(), start=1):
            values[key] = (
                f"postgresql+psycopg://role_{index}:password-{index}-bounded"
                "@10.2.64.2:5432/nexusdesk"
            )
        return values

    def _write_fixture(self, root: Path) -> tuple[Path, Path, Path]:
        manifest_path = root / "manifest.json"
        manifest_path.write_text(json.dumps(self._manifest()), encoding="utf-8")
        compose_path = root / "compose.yml"
        compose_path.write_text(self._compose(), encoding="utf-8")
        env_path = root / ".env.controlled"
        env_path.write_text(
            "".join(f"{key}={value}\n" for key, value in self._values().items()),
            encoding="utf-8",
        )
        return env_path, compose_path, manifest_path

    def _validate(self, env_path: Path, compose_path: Path, manifest_path: Path):
        return MODULE.validate(
            env_path=env_path,
            compose_path=compose_path,
            manifest_path=manifest_path,
            expected_database_host="10.2.64.2",
            expected_database_port=5432,
            expected_domain="mcs.speedaf.com",
            check_host_paths=False,
        )

    def _assert_env_failure(self, mutate, error: str) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env_path, compose_path, manifest_path = self._write_fixture(root)
            values = self._values()
            mutate(values)
            env_path.write_text(
                "".join(f"{key}={value}\n" for key, value in values.items()),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(MODULE.PreflightError, error):
                self._validate(env_path, compose_path, manifest_path)

    def test_accepts_digest_only_safe_cutover_with_distinct_database_roles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path, compose_path, manifest_path = self._write_fixture(Path(directory))
            payload = self._validate(env_path, compose_path, manifest_path)
        self.assertEqual(payload["status"], "pass")
        self.assertEqual(set(payload["database_roles"]), set(MODULE.DATABASE_ROLE_KEYS))
        self.assertEqual(
            len({row["username"] for row in payload["database_roles"].values()}),
            len(MODULE.DATABASE_ROLE_KEYS),
        )
        self.assertFalse(payload["database_passwords_included"])
        self.assertFalse(payload["external_effects_enabled"])

    def test_rejects_mutable_image_tag(self) -> None:
        self._assert_env_failure(
            lambda values: values.update(
                CONTROLLED_IMAGE="ghcr.io/maximvonshaft/nexus_helpdesk:latest",
                IMAGE_TAG="ghcr.io/maximvonshaft/nexus_helpdesk:latest",
            ),
            "controlled_image_not_digest_pinned",
        )

    def test_rejects_reenabled_provider_or_wrong_mode(self) -> None:
        self._assert_env_failure(
            lambda values: values.update(PROVIDER_RUNTIME_CANARY_PERCENT="100"),
            "unsafe_control:PROVIDER_RUNTIME_CANARY_PERCENT",
        )
        self._assert_env_failure(
            lambda values: values.update(PROVIDER_RUNTIME_TRAFFIC_MODE="canary"),
            "unsafe_control:PROVIDER_RUNTIME_TRAFFIC_MODE",
        )

    def test_rejects_compose_build_shared_env_or_missing_livekit_service(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env_path, compose_path, manifest_path = self._write_fixture(root)
            document = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
            document["services"]["app-controlled"]["build"] = "."
            compose_path.write_text(yaml.safe_dump(document), encoding="utf-8")
            with self.assertRaisesRegex(MODULE.PreflightError, "compose_build_forbidden"):
                self._validate(env_path, compose_path, manifest_path)

            document = yaml.safe_load(self._compose())
            document["services"]["app-controlled"]["env_file"] = ".env.controlled"
            compose_path.write_text(yaml.safe_dump(document), encoding="utf-8")
            with self.assertRaisesRegex(MODULE.PreflightError, "compose_shared_env_file_forbidden"):
                self._validate(env_path, compose_path, manifest_path)

            document = yaml.safe_load(self._compose())
            del document["services"]["livekit-agent-controlled"]
            compose_path.write_text(yaml.safe_dump(document), encoding="utf-8")
            with self.assertRaisesRegex(MODULE.PreflightError, "compose_service_missing"):
                self._validate(env_path, compose_path, manifest_path)

    def test_rejects_generic_duplicate_or_wrong_port_database_authority(self) -> None:
        self._assert_env_failure(
            lambda values: values.update(
                DATABASE_URL="postgresql+psycopg://generic:generic-pass@10.2.64.2:5432/nexusdesk"
            ),
            "disabled_capability_credential_forbidden:DATABASE_URL",
        )
        self._assert_env_failure(
            lambda values: values.update(
                DATABASE_URL_OUTBOUND=values["DATABASE_URL_APP"].replace(
                    "password-2-bounded", "password-3-bounded"
                )
            ),
            "database_role_usernames_must_be_distinct",
        )
        self._assert_env_failure(
            lambda values: values.update(
                DATABASE_URL_OUTBOUND=values["DATABASE_URL_OUTBOUND"].replace(":5432/", ":6432/")
            ),
            "database_port_mismatch:DATABASE_URL_OUTBOUND",
        )

    def test_rejects_disabled_credentials_invalid_secrets_or_unsafe_dev_auth(self) -> None:
        self._assert_env_failure(
            lambda values: values.update(AI_RUNTIME_TOKEN_HOST_PATH="/tmp/token"),
            "disabled_capability_credential_forbidden:AI_RUNTIME_TOKEN_HOST_PATH",
        )
        self._assert_env_failure(
            lambda values: values.update(SECRET_KEY="short"),
            "secret_invalid:SECRET_KEY",
        )
        self._assert_env_failure(
            lambda values: values.update(ALLOW_DEV_AUTH="true"),
            "unsafe_control:ALLOW_DEV_AUTH",
        )

    def test_rejects_build_metadata_mismatch(self) -> None:
        self._assert_env_failure(
            lambda values: values.update(BUILD_TIME="20260713T190001Z"),
            "build_time_manifest_mismatch",
        )


if __name__ == "__main__":
    unittest.main()
