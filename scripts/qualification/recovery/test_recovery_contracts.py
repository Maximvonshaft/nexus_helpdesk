from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_MODULE = Path(__file__).with_name("build_recovery_evidence.py")


def _load_evidence_module():
    if not EVIDENCE_MODULE.is_file():
        raise ImportError("recovery evidence builder is missing")
    spec = importlib.util.spec_from_file_location("nexus_recovery_evidence", EVIDENCE_MODULE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _run(script: Path, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _fake_commands(root: Path, commands: tuple[str, ...], marker: Path) -> Path:
    fake_bin = root / "bin"
    fake_bin.mkdir()
    for command in commands:
        _write_executable(
            fake_bin / command,
            f"#!/usr/bin/env bash\ntouch {marker!s}\nexit 0\n",
        )
    return fake_bin


def _env(fake_bin: Path, **updates: str) -> dict[str, str]:
    value = os.environ.copy()
    value.update({"PATH": f"{fake_bin}:{value.get('PATH', '')}", **updates})
    return value


def _write_bundle(
    root: Path,
    *,
    source_database: str = "nexus_source",
    head: str = "20260713_0059",
    vector_version: str = "0.8.0",
) -> Path:
    bundle = root / "bundle"
    bundle.mkdir()
    archive = bundle / "database.dump"
    archive_bytes = b"synthetic custom-format archive"
    archive.write_bytes(archive_bytes)
    (bundle / "backup_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "nexus_postgres_backup_manifest_v1",
                "format": "postgres_custom",
                "archive": "database.dump",
                "archive_sha256": "sha256:" + hashlib.sha256(archive_bytes).hexdigest(),
                "archive_size_bytes": len(archive_bytes),
                "source_database_sha256": hashlib.sha256(source_database.encode()).hexdigest(),
                "alembic_head": head,
                "preinstalled_extensions": [{"name": "vector", "version": vector_version}],
                "created_at": "2026-07-13T00:00:00Z",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    return bundle


class OperatorRecoveryContractTests(unittest.TestCase):
    def test_operator_scripts_expose_fail_closed_contracts(self) -> None:
        backup = (ROOT / "scripts" / "deploy" / "backup_postgres.sh").read_text(encoding="utf-8")
        rollback = (ROOT / "scripts" / "deploy" / "rollback_release.sh").read_text(encoding="utf-8")
        for token in (
            "POSTGRES_NATIVE_URL",
            "mktemp",
            "pg_restore --list",
            "sha256sum",
            "backup_manifest",
            "source_database_sha256",
            "preinstalled_extensions",
            "VECTOR_VERSION",
            "mv -T --",
            "postgres_native_url_user_required",
            "postgres_native_url_query_not_allowed",
            "postgres_native_url_fragment_not_allowed",
        ):
            self.assertIn(token, backup)
        self.assertNotIn('pg_dump "$DATABASE_URL"', backup)
        for token in (
            "POSTGRES_NATIVE_URL",
            "ON_ERROR_STOP=1",
            "--single-transaction",
            "--use-list=",
            "backup_restore_vector_toc_invalid",
            "preinstalled_extensions",
            "DATABASE_RESTORE_APPLIED",
            "DATABASE_RESTORED",
            "IMAGE_RESTARTED",
            "HEALTH_VERIFIED",
            "failure_stage",
            "http_code",
            "postgres_native_url_user_required",
            "postgres_native_url_query_not_allowed",
            "postgres_native_url_fragment_not_allowed",
        ):
            self.assertIn(token, rollback)
        self.assertNotIn("Rollback helper completed.", rollback)

    def test_operator_scripts_reject_ambient_user_and_url_overrides_before_native_clients(self) -> None:
        backup = ROOT / "scripts" / "deploy" / "backup_postgres.sh"
        rollback = ROOT / "scripts" / "deploy" / "rollback_release.sh"
        cases = (
            ("postgresql://nexus@db-a:5432/nexus_restore?host=db-b", "postgres_native_url_query_not_allowed"),
            ("postgresql://nexus@db-a:5432/nexus_restore#host=db-b", "postgres_native_url_fragment_not_allowed"),
            ("postgresql://db-a:5432/nexus_restore", "postgres_native_url_user_required"),
        )
        for native_url, reason in cases:
            with self.subTest(script="backup", reason=reason), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                marker = root / "native-called"
                fake_bin = _fake_commands(root, ("pg_dump", "pg_restore", "psql"), marker)
                completed = _run(
                    backup,
                    str(root / "backups"),
                    env=_env(fake_bin, POSTGRES_NATIVE_URL=native_url.replace("nexus_restore", "nexus_source")),
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn(reason, completed.stderr)
                self.assertFalse(marker.exists())
            with self.subTest(script="rollback", reason=reason), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                marker = root / "native-called"
                fake_bin = _fake_commands(root, ("pg_restore", "psql"), marker)
                completed = _run(
                    rollback,
                    str(root / "bundle"),
                    env=_env(
                        fake_bin,
                        ROLLBACK_CONFIRM="I_UNDERSTAND",
                        POSTGRES_NATIVE_URL=native_url,
                        ROLLBACK_STATUS_FILE=str(root / "rollback-result.json"),
                    ),
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn(reason, completed.stderr)
                self.assertFalse(marker.exists())

    def _runner_env(self, fake_bin: Path) -> dict[str, str]:
        return _env(
            fake_bin,
            SOURCE_APP_URL="postgresql+psycopg://nexus_recovery_source:source-test@db-a:5432/nexus_source",
            SOURCE_NATIVE_URL="postgresql://nexus_recovery_source:source-test@db-a:5432/nexus_source",
            RESTORE_APP_URL="postgresql+psycopg://nexus_recovery_restore:restore-test@db-a:5432/nexus_restore",
            RESTORE_NATIVE_URL="postgresql://nexus_recovery_restore:restore-test@db-a:5432/nexus_restore",
            RECOVERY_ADMIN_NATIVE_URL="postgresql://nexus_recovery_admin:admin-test@db-b:5432/postgres",
            RECOVERY_ALLOW_DATABASE_RECREATE="I_UNDERSTAND",
            SOURCE_SHA="a" * 40,
        )

    def test_runner_refuses_unsafe_authority_before_psql(self) -> None:
        runner = ROOT / "scripts" / "qualification" / "recovery" / "run_recovery_qualification.sh"
        cases = (
            ({}, "recovery_admin_cluster_mismatch"),
            (
                {
                    "SOURCE_APP_URL": "postgresql+psycopg://nexus_recovery_source:source-test@db-a:5432/nexus_source?host=db-b",
                    "SOURCE_NATIVE_URL": "postgresql://nexus_recovery_source:source-test@db-a:5432/nexus_source?host=db-b",
                    "RECOVERY_ADMIN_NATIVE_URL": "postgresql://nexus_recovery_admin:admin-test@db-a:5432/postgres",
                },
                "recovery_url_query_not_allowed",
            ),
            (
                {
                    "SOURCE_APP_URL": "postgresql+psycopg://db-a:5432/nexus_source",
                    "SOURCE_NATIVE_URL": "postgresql://db-a:5432/nexus_source",
                    "RECOVERY_ADMIN_NATIVE_URL": "postgresql://nexus_recovery_admin:admin-test@db-a:5432/postgres",
                },
                "recovery_url_user_required",
            ),
            (
                {
                    "SOURCE_APP_URL": "postgresql+psycopg://nexus_recovery_admin:admin-test@db-a:5432/nexus_source",
                    "SOURCE_NATIVE_URL": "postgresql://nexus_recovery_admin:admin-test@db-a:5432/nexus_source",
                    "RECOVERY_ADMIN_NATIVE_URL": "postgresql://nexus_recovery_admin:admin-test@db-a:5432/postgres",
                },
                "recovery_user_name_mismatch",
            ),
        )
        for updates, expected in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                marker = root / "psql-called"
                fake_bin = _fake_commands(root, ("psql",), marker)
                env = self._runner_env(fake_bin)
                env.update(updates)
                completed = _run(runner, env=env)
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn(expected, completed.stderr)
                self.assertFalse(marker.exists())

    def test_restore_preserves_manifest_bound_vector_and_records_post_verify_failure(self) -> None:
        rollback = ROOT / "scripts" / "deploy" / "rollback_release.sh"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = _write_bundle(root)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            restore_marker = root / "restore-applied"
            list_marker = root / "list-filtered"
            _write_executable(
                fake_bin / "pg_restore",
                "#!/usr/bin/env bash\n"
                "if [[ \"${1:-}\" == \"--list\" ]]; then\n"
                "  printf '; archive\\n1; 3079 100 EXTENSION - vector nexus_recovery_admin\\n2; 0 0 COMMENT - EXTENSION vector nexus_recovery_admin\\n3; 1259 101 TABLE public sample nexus_recovery_source\\n'\n"
                "  exit 0\n"
                "fi\n"
                "list=''\n"
                "for arg in \"$@\"; do case \"$arg\" in --use-list=*) list=\"${arg#--use-list=}\" ;; esac; done\n"
                "test -n \"$list\"\n"
                "grep -q '^;1; .* EXTENSION - vector ' \"$list\"\n"
                "grep -q '^;2; .* COMMENT - EXTENSION vector ' \"$list\"\n"
                "grep -q '^3; .* TABLE public sample ' \"$list\"\n"
                f"touch {list_marker!s}\n"
                f"touch {restore_marker!s}\n"
                "exit 0\n",
            )
            _write_executable(
                fake_bin / "psql",
                "#!/usr/bin/env bash\n"
                "case \"$*\" in\n"
                "  *'SELECT current_database()'*) printf 'nexus_restore\\n' ;;\n"
                "  *\"SELECT extversion FROM pg_extension WHERE extname = 'vector'\"*) printf '0.8.0\\n' ;;\n"
                "  *'SELECT version_num FROM alembic_version'*) printf '20260713_0058\\n' ;;\n"
                "  *) exit 1 ;;\n"
                "esac\n",
            )
            status = root / "rollback-result.json"
            completed = _run(
                rollback,
                str(bundle),
                env=_env(
                    fake_bin,
                    ROLLBACK_CONFIRM="I_UNDERSTAND",
                    POSTGRES_NATIVE_URL="postgresql://nexus_recovery_restore:restore-test@db-a:5432/nexus_restore",
                    ROLLBACK_STATUS_FILE=str(status),
                ),
            )
            payload = json.loads(status.read_text(encoding="utf-8"))
            self.assertTrue(list_marker.exists())
            self.assertTrue(restore_marker.exists())
        self.assertEqual(completed.returncode, 7)
        self.assertEqual(payload["failure_stage"], "DATABASE_POST_VERIFY")
        self.assertIn("DATABASE_RESTORE_APPLIED", payload["states"])
        self.assertNotIn("DATABASE_RESTORED", payload["states"])
        self.assertTrue(payload["database_restore_applied"])
        self.assertFalse(payload["database_restored"])

    def test_restore_refuses_missing_preinstalled_vector_before_pg_restore(self) -> None:
        rollback = ROOT / "scripts" / "deploy" / "rollback_release.sh"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = _write_bundle(root)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            restore_marker = root / "restore-called"
            _write_executable(fake_bin / "pg_restore", f"#!/usr/bin/env bash\ntouch {restore_marker!s}\nexit 0\n")
            _write_executable(
                fake_bin / "psql",
                "#!/usr/bin/env bash\n"
                "case \"$*\" in\n"
                "  *'SELECT current_database()'*) printf 'nexus_restore\\n' ;;\n"
                "  *\"SELECT extversion FROM pg_extension WHERE extname = 'vector'\"*) printf '\\n' ;;\n"
                "  *) exit 1 ;;\n"
                "esac\n",
            )
            completed = _run(
                rollback,
                str(bundle),
                env=_env(
                    fake_bin,
                    ROLLBACK_CONFIRM="I_UNDERSTAND",
                    POSTGRES_NATIVE_URL="postgresql://nexus_recovery_restore:restore-test@db-a:5432/nexus_restore",
                    ROLLBACK_STATUS_FILE=str(root / "rollback-result.json"),
                ),
            )
            self.assertFalse(restore_marker.exists())
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("Target vector extension version does not match backup manifest", completed.stderr)

    def test_health_requires_2xx_and_writes_partial_state(self) -> None:
        rollback = ROOT / "scripts" / "deploy" / "rollback_release.sh"
        for curl_script in ("#!/usr/bin/env bash\nexit 22\n", "#!/usr/bin/env bash\nprintf '302'\n"):
            with self.subTest(curl_script=curl_script), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                fake_bin = root / "bin"
                fake_bin.mkdir()
                _write_executable(fake_bin / "docker", "#!/usr/bin/env bash\nexit 0\n")
                _write_executable(fake_bin / "curl", curl_script)
                status = root / "rollback-result.json"
                completed = _run(
                    rollback,
                    env=_env(
                        fake_bin,
                        ROLLBACK_CONFIRM="I_UNDERSTAND",
                        OLD_IMAGE_TAG="nexus:test-old",
                        ROLLBACK_HEALTH_URL="http://127.0.0.1:18082",
                        ROLLBACK_STATUS_FILE=str(status),
                    ),
                )
                payload = json.loads(status.read_text(encoding="utf-8"))
                self.assertNotEqual(completed.returncode, 0)
                self.assertEqual(payload["failure_stage"], "HEALTH_VERIFICATION")
                self.assertIn("IMAGE_RESTARTED", payload["states"])
                self.assertNotIn("HEALTH_VERIFIED", payload["states"])

    def test_workflow_proves_role_extension_separation_and_quarantine(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "osr-recovery-qualification.yml").read_text(encoding="utf-8")
        runner = (ROOT / "scripts" / "qualification" / "recovery" / "run_recovery_qualification.sh").read_text(encoding="utf-8")
        self.assertIn("POSTGRES_USER: nexus_recovery_admin", workflow)
        self.assertIn("CREATE ROLE nexus_recovery_source", workflow)
        self.assertIn("CREATE ROLE nexus_recovery_restore", workflow)
        self.assertNotIn("PGUSER:", workflow)
        self.assertIn("CREATE DATABASE nexus_source WITH OWNER nexus_recovery_source TEMPLATE template0", runner)
        self.assertIn("CREATE DATABASE nexus_restore WITH OWNER nexus_recovery_restore TEMPLATE template0", runner)
        self.assertIn("CREATE EXTENSION vector", runner)
        self.assertIn("recovery_vector_preinstall_proof_failed", runner)
        self.assertIn("recovery_role_identity_collision", runner)
        clean = workflow.index("- name: Upload clean bounded qualification evidence")
        failure = workflow.index("- name: Upload sanitized recovery failure status")
        enforce = workflow.index("- name: Enforce recovery qualification")
        self.assertLess(clean, failure)
        self.assertLess(failure, enforce)
        self.assertIn("steps.artifact_scan.outputs.exit_code == '0'", workflow[clean:failure])
        self.assertIn("artifacts/recovery/*.json", workflow[clean:failure])
        self.assertIn("steps.artifact_scan.outputs.exit_code != '0'", workflow[failure:enforce])
        self.assertIn("qualification-status.json", workflow[failure:enforce])
        self.assertNotIn("artifacts/recovery/*.json", workflow[failure:enforce])


class RecoveryEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _load_evidence_module()

    def _snapshot(self, *, head: str = "20260711_0058", markets: int = 1, marker: int = 1) -> dict:
        return {
            "schema_version": "nexus_recovery_snapshot_v1",
            "alembic_head": head,
            "table_count": 3,
            "tables": {"markets": markets, "teams": 1, "service_heartbeats": 0},
            "foreign_key_signature_count": 1,
            "foreign_key_signatures": ["a" * 64],
            "invalid_foreign_key_count": 0,
            "synthetic_marker_count": marker,
        }

    def _compare(self, root: Path, source_payload: dict, restored_payload: dict, **overrides):
        source = root / "source.json"
        restored = root / "restored.json"
        output = root / "evidence.json"
        source.write_text(json.dumps(source_payload), encoding="utf-8")
        restored.write_text(json.dumps(restored_payload), encoding="utf-8")
        values = {
            "source_sha": "a" * 40,
            "backup_sha256": "sha256:" + "b" * 64,
            "marker_committed_at": "2026-07-13T00:00:00+00:00",
            "backup_completed_at": "2026-07-13T00:00:05+00:00",
            "restore_started_at": "2026-07-13T00:00:06+00:00",
            "restore_completed_at": "2026-07-13T00:00:16+00:00",
            "rto_target_seconds": 120,
            "rpo_target_seconds": 60,
        }
        values.update(overrides)
        code = self.module.compare(source, restored, output, **values)
        return code, json.loads(output.read_text(encoding="utf-8"))

    def test_matching_restore_passes_with_bounded_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            code, evidence = self._compare(Path(directory), self._snapshot(), self._snapshot())
        self.assertEqual(code, 0)
        self.assertEqual(evidence["status"], "pass")
        self.assertTrue(evidence["foreign_key_definitions_match"])
        self.assertFalse(evidence["production_data_used"])
        self.assertFalse(evidence["production_mutation_performed"])

    def test_schema_data_marker_and_fk_failures_are_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            restored = self._snapshot(head="wrong", markets=2, marker=0)
            restored["invalid_foreign_key_count"] = 1
            restored["foreign_key_signatures"] = ["b" * 64]
            code, evidence = self._compare(Path(directory), self._snapshot(), restored)
        self.assertEqual(code, 1)
        for reason in (
            "recovery.alembic_head_mismatch",
            "recovery.table_count_mismatch",
            "recovery.synthetic_marker_missing",
            "recovery.foreign_key_not_validated",
            "recovery.foreign_key_definition_mismatch",
        ):
            self.assertIn(reason, evidence["reasons"])

    def test_invalid_fk_signature_metadata_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            restored = self._snapshot()
            restored.pop("foreign_key_signatures")
            code, evidence = self._compare(Path(directory), self._snapshot(), restored)
        self.assertEqual(code, 1)
        self.assertIn("recovery.foreign_key_signature_invalid", evidence["reasons"])

    def test_rto_rpo_order_bounds_and_digest_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code, evidence = self._compare(
                root,
                self._snapshot(),
                self._snapshot(),
                backup_completed_at="2026-07-13T00:02:00+00:00",
                restore_completed_at="2026-07-13T00:03:00+00:00",
                rto_target_seconds=30,
                rpo_target_seconds=30,
            )
            self.assertEqual(code, 1)
            self.assertIn("recovery.rto_exceeded", evidence["reasons"])
            self.assertIn("recovery.rpo_exceeded", evidence["reasons"])
            code, evidence = self._compare(
                root,
                self._snapshot(),
                self._snapshot(),
                marker_committed_at="2026-07-13T00:00:10+00:00",
                backup_completed_at="2026-07-13T00:00:05+00:00",
                restore_started_at="2026-07-13T00:00:20+00:00",
                restore_completed_at="2026-07-13T00:00:15+00:00",
            )
            self.assertEqual(code, 1)
            self.assertIn("recovery.rpo_timestamp_order_invalid", evidence["reasons"])
            self.assertIn("recovery.rto_timestamp_order_invalid", evidence["reasons"])
            with self.assertRaisesRegex(self.module.RecoveryEvidenceError, "backup_sha"):
                self._compare(root, self._snapshot(), self._snapshot(), backup_sha256="invalid")

    def test_migration_repair_plan_is_deterministic_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "plan.json"
            code = self.module.migration_plan(
                observed_heads=("20260710_0057",),
                expected_head="20260711_0058",
                output=output,
            )
            plan = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(code, 1)
            self.assertEqual(plan["action"], "alembic_upgrade_head")
            self.assertFalse(plan["apply_authorized"])
            for observed, reason in (
                (("20260710_0057", "20260711_0058"), "migration_heads_multiple"),
                ((), "migration_head_missing"),
            ):
                with self.assertRaisesRegex(self.module.RecoveryEvidenceError, reason):
                    self.module.migration_plan(
                        observed_heads=observed,
                        expected_head="20260711_0058",
                        output=output,
                    )


if __name__ == "__main__":
    unittest.main()
