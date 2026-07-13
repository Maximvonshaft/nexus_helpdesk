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


class OperatorRecoveryContractTests(unittest.TestCase):
    def test_backup_uses_native_libpq_url_and_atomic_finalization(self) -> None:
        script = (ROOT / "scripts" / "deploy" / "backup_postgres.sh").read_text(encoding="utf-8")
        self.assertIn("POSTGRES_NATIVE_URL", script)
        self.assertNotIn('pg_dump "$DATABASE_URL"', script)
        self.assertIn("mktemp", script)
        self.assertIn("pg_restore --list", script)
        self.assertIn("sha256sum", script)
        self.assertIn("backup_manifest", script)
        self.assertIn("source_database_sha256", script)
        self.assertIn("mv -T --", script)

    def test_rollback_fails_fast_and_reports_explicit_states(self) -> None:
        script = (ROOT / "scripts" / "deploy" / "rollback_release.sh").read_text(encoding="utf-8")
        self.assertIn("POSTGRES_NATIVE_URL", script)
        self.assertIn("ON_ERROR_STOP=1", script)
        self.assertIn("--single-transaction", script)
        self.assertIn("source_database_sha256", script)
        self.assertIn("archive_size_bytes", script)
        self.assertIn("ROLLBACK_ALLOW_IN_PLACE", script)
        self.assertIn("INSTRUCTIONS_ONLY", script)
        self.assertIn("DATABASE_RESTORE_APPLIED", script)
        self.assertIn("DATABASE_RESTORED", script)
        self.assertIn("IMAGE_RESTARTED", script)
        self.assertIn("HEALTH_VERIFIED", script)
        self.assertIn("failure_stage", script)
        self.assertIn("http_code", script)
        self.assertNotIn("Rollback helper completed.", script)

    def _runner_env(self, fake_bin: Path) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{fake_bin}:{env.get('PATH', '')}",
                "SOURCE_APP_URL": "postgresql+psycopg://nexus@db-a:5432/nexus_source",
                "SOURCE_NATIVE_URL": "postgresql://nexus@db-a:5432/nexus_source",
                "RESTORE_APP_URL": "postgresql+psycopg://nexus@db-a:5432/nexus_restore",
                "RESTORE_NATIVE_URL": "postgresql://nexus@db-a:5432/nexus_restore",
                "RECOVERY_ADMIN_NATIVE_URL": "postgresql://nexus@db-b:5432/postgres",
                "RECOVERY_ALLOW_DATABASE_RECREATE": "I_UNDERSTAND",
                "SOURCE_SHA": "a" * 40,
            }
        )
        return env

    def test_runner_refuses_mismatched_admin_cluster_before_psql(self) -> None:
        runner = ROOT / "scripts" / "qualification" / "recovery" / "run_recovery_qualification.sh"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "psql-called"
            fake_bin = root / "bin"
            fake_bin.mkdir()
            _write_executable(
                fake_bin / "psql",
                f"#!/usr/bin/env bash\ntouch {marker!s}\nexit 0\n",
            )
            completed = subprocess.run(
                ["bash", str(runner)],
                cwd=ROOT,
                env=self._runner_env(fake_bin),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertFalse(marker.exists())
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("recovery_admin_cluster_mismatch", completed.stderr)

    def test_runner_rejects_libpq_query_overrides_before_psql(self) -> None:
        runner = ROOT / "scripts" / "qualification" / "recovery" / "run_recovery_qualification.sh"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "psql-called"
            fake_bin = root / "bin"
            fake_bin.mkdir()
            _write_executable(
                fake_bin / "psql",
                f"#!/usr/bin/env bash\ntouch {marker!s}\nexit 0\n",
            )
            env = self._runner_env(fake_bin)
            env.update(
                {
                    "SOURCE_APP_URL": "postgresql+psycopg://nexus@db-a:5432/nexus_source?host=db-b",
                    "SOURCE_NATIVE_URL": "postgresql://nexus@db-a:5432/nexus_source?host=db-b",
                    "RESTORE_APP_URL": "postgresql+psycopg://nexus@db-a:5432/nexus_restore",
                    "RESTORE_NATIVE_URL": "postgresql://nexus@db-a:5432/nexus_restore",
                    "RECOVERY_ADMIN_NATIVE_URL": "postgresql://nexus@db-a:5432/postgres",
                }
            )
            completed = subprocess.run(
                ["bash", str(runner)],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertFalse(marker.exists())
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("recovery_url_query_not_allowed", completed.stderr)

    def _run_image_health_fixture(self, *, curl_script: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        script = ROOT / "scripts" / "deploy" / "rollback_release.sh"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            _write_executable(fake_bin / "docker", "#!/usr/bin/env bash\nexit 0\n")
            _write_executable(fake_bin / "curl", curl_script)
            status = root / "rollback-result.json"
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env.get('PATH', '')}",
                    "ROLLBACK_CONFIRM": "I_UNDERSTAND",
                    "OLD_IMAGE_TAG": "nexus:test-old",
                    "ROLLBACK_HEALTH_URL": "http://127.0.0.1:18082",
                    "ROLLBACK_STATUS_FILE": str(status),
                }
            )
            completed = subprocess.run(
                ["bash", str(script)],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            payload = json.loads(status.read_text(encoding="utf-8"))
        return completed, payload

    def test_database_post_verify_failure_records_applied_restore(self) -> None:
        script = ROOT / "scripts" / "deploy" / "rollback_release.sh"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            fake_bin.mkdir()
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
                        "source_database_sha256": hashlib.sha256(b"nexus_source").hexdigest(),
                        "alembic_head": "20260713_0059",
                        "created_at": "2026-07-13T00:00:00Z",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            restore_marker = root / "restore-applied"
            _write_executable(
                fake_bin / "pg_restore",
                "#!/usr/bin/env bash\n"
                "if [[ \"${1:-}\" == \"--list\" ]]; then exit 0; fi\n"
                f"touch {restore_marker!s}\n"
                "exit 0\n",
            )
            _write_executable(
                fake_bin / "psql",
                "#!/usr/bin/env bash\n"
                "args=\"$*\"\n"
                "if [[ \"$args\" == *\"SELECT current_database()\"* ]]; then\n"
                "  printf 'nexus_restore\\n'\n"
                "  exit 0\n"
                "fi\n"
                "if [[ \"$args\" == *\"SELECT version_num FROM alembic_version\"* ]]; then\n"
                "  printf '20260713_0058\\n'\n"
                "  exit 0\n"
                "fi\n"
                "exit 1\n",
            )
            status = root / "rollback-result.json"
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env.get('PATH', '')}",
                    "ROLLBACK_CONFIRM": "I_UNDERSTAND",
                    "POSTGRES_NATIVE_URL": "postgresql://nexus@db-a:5432/nexus_restore",
                    "ROLLBACK_STATUS_FILE": str(status),
                }
            )
            completed = subprocess.run(
                ["bash", str(script), str(bundle)],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            payload = json.loads(status.read_text(encoding="utf-8"))
            self.assertTrue(restore_marker.exists())

        self.assertEqual(completed.returncode, 7)
        self.assertEqual(payload["outcome"], "fail")
        self.assertEqual(payload["failure_stage"], "DATABASE_POST_VERIFY")
        self.assertIn("DATABASE_RESTORE_APPLIED", payload["states"])
        self.assertNotIn("DATABASE_RESTORED", payload["states"])
        self.assertTrue(payload["database_restore_applied"])
        self.assertFalse(payload["database_restored"])

    def test_health_failure_writes_partial_rollback_status(self) -> None:
        completed, payload = self._run_image_health_fixture(curl_script="#!/usr/bin/env bash\nexit 22\n")
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(payload["outcome"], "fail")
        self.assertEqual(payload["failure_stage"], "HEALTH_VERIFICATION")
        self.assertIn("IMAGE_RESTARTED", payload["states"])
        self.assertFalse(payload["health_verified"])

    def test_health_redirect_is_not_accepted_as_verified(self) -> None:
        completed, payload = self._run_image_health_fixture(
            curl_script="#!/usr/bin/env bash\nprintf '302'\nexit 0\n"
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(payload["outcome"], "fail")
        self.assertEqual(payload["failure_stage"], "HEALTH_VERIFICATION")
        self.assertIn("IMAGE_RESTARTED", payload["states"])
        self.assertNotIn("HEALTH_VERIFIED", payload["states"])

    def test_workflow_quarantines_unsafe_evidence(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "osr-recovery-qualification.yml").read_text(encoding="utf-8")
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

    def _write(self, root: Path, name: str, payload: dict) -> Path:
        path = root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _snapshot(self, *, head: str = "20260711_0058", markets: int = 1, marker: int = 1) -> dict:
        return {
            "schema_version": "nexus_recovery_snapshot_v1",
            "alembic_head": head,
            "table_count": 3,
            "tables": {"markets": markets, "teams": 1, "service_heartbeats": 0},
            "invalid_foreign_key_count": 0,
            "synthetic_marker_count": marker,
        }

    def _compare(self, root: Path, source_payload: dict, restored_payload: dict, **overrides):
        source = self._write(root, "source.json", source_payload)
        restored = self._write(root, "restored.json", restored_payload)
        output = root / "evidence.json"
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
        self.assertFalse(evidence["production_data_used"])
        self.assertFalse(evidence["production_mutation_performed"])

    def test_head_row_marker_and_fk_mismatch_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            restored = self._snapshot(head="wrong", markets=2, marker=0)
            restored["invalid_foreign_key_count"] = 1
            code, evidence = self._compare(Path(directory), self._snapshot(), restored)
        self.assertEqual(code, 1)
        self.assertIn("recovery.alembic_head_mismatch", evidence["reasons"])
        self.assertIn("recovery.table_count_mismatch", evidence["reasons"])
        self.assertIn("recovery.synthetic_marker_missing", evidence["reasons"])
        self.assertIn("recovery.foreign_key_not_validated", evidence["reasons"])

    def test_rto_rpo_and_invalid_digest_fail_closed(self) -> None:
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
            self.assertEqual(plan["status"], "repair_required")
            self.assertEqual(plan["action"], "alembic_upgrade_head")
            self.assertFalse(plan["apply_authorized"])
            self.assertFalse(plan["production_mutation_performed"])

            with self.assertRaisesRegex(self.module.RecoveryEvidenceError, "migration_heads_multiple"):
                self.module.migration_plan(
                    observed_heads=("20260710_0057", "20260711_0058"),
                    expected_head="20260711_0058",
                    output=output,
                )
            with self.assertRaisesRegex(self.module.RecoveryEvidenceError, "migration_head_missing"):
                self.module.migration_plan(
                    observed_heads=(),
                    expected_head="20260711_0058",
                    output=output,
                )


if __name__ == "__main__":
    unittest.main()
