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
BUILDER = ROOT / "scripts" / "qualification" / "recovery" / "build_recovery_evidence.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("nexus_recovery_evidence_review", BUILDER)
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


def _env(fake_bin: Path, **updates: str) -> dict[str, str]:
    value = os.environ.copy()
    value.update({"PATH": f"{fake_bin}:{value.get('PATH', '')}", **updates})
    return value


def _write_bundle(root: Path) -> Path:
    bundle = root / "bundle"
    bundle.mkdir()
    archive = bundle / "database.dump"
    archive_bytes = b"synthetic custom archive"
    archive.write_bytes(archive_bytes)
    manifest = {
        "schema_version": "nexus_postgres_backup_manifest_v1",
        "format": "postgres_custom",
        "archive": "database.dump",
        "archive_sha256": "sha256:" + hashlib.sha256(archive_bytes).hexdigest(),
        "archive_size_bytes": len(archive_bytes),
        "source_database_sha256": hashlib.sha256(b"nexus_source").hexdigest(),
        "alembic_head": "20260713_0059",
        "preinstalled_extensions": [{"name": "vector", "version": "0.8.0"}],
        "created_at": "2026-07-13T00:00:00Z",
    }
    (bundle / "backup_manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return bundle


class RecoveryReviewRegressionTests(unittest.TestCase):
    def test_native_url_guards_reject_multi_host_before_clients(self) -> None:
        backup = ROOT / "scripts" / "deploy" / "backup_postgres.sh"
        rollback = ROOT / "scripts" / "deploy" / "rollback_release.sh"
        native_url = "postgresql://nexus:secret@primary,standby:5432/nexus_restore"
        for script, args, updates in (
            (
                backup,
                ("unused",),
                {"POSTGRES_NATIVE_URL": native_url.replace("nexus_restore", "nexus_source")},
            ),
            (
                rollback,
                ("missing-bundle",),
                {
                    "ROLLBACK_CONFIRM": "I_UNDERSTAND",
                    "POSTGRES_NATIVE_URL": native_url,
                },
            ),
        ):
            with self.subTest(script=script.name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                marker = root / "client-called"
                fake_bin = root / "bin"
                fake_bin.mkdir()
                for command in ("pg_dump", "pg_restore", "psql"):
                    _write_executable(
                        fake_bin / command,
                        f"#!/usr/bin/env bash\ntouch {marker!s}\nexit 0\n",
                    )
                completed = _run(
                    script,
                    *args,
                    env=_env(
                        fake_bin,
                        ROLLBACK_STATUS_FILE=str(root / "rollback-result.json"),
                        **updates,
                    ),
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("postgres_native_url_multi_host_not_allowed", completed.stderr)
                self.assertFalse(marker.exists())

    def test_qualification_runner_rejects_multi_host_before_psql(self) -> None:
        runner = ROOT / "scripts" / "qualification" / "recovery" / "run_recovery_qualification.sh"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "psql-called"
            fake_bin = root / "bin"
            fake_bin.mkdir()
            _write_executable(fake_bin / "psql", f"#!/usr/bin/env bash\ntouch {marker!s}\nexit 0\n")
            host = "primary,standby"
            completed = _run(
                runner,
                env=_env(
                    fake_bin,
                    SOURCE_APP_URL=f"postgresql+psycopg://nexus_recovery_source:source@{host}:5432/nexus_source",
                    SOURCE_NATIVE_URL=f"postgresql://nexus_recovery_source:source@{host}:5432/nexus_source",
                    RESTORE_APP_URL=f"postgresql+psycopg://nexus_recovery_restore:restore@{host}:5432/nexus_restore",
                    RESTORE_NATIVE_URL=f"postgresql://nexus_recovery_restore:restore@{host}:5432/nexus_restore",
                    RECOVERY_ADMIN_NATIVE_URL=f"postgresql://nexus_recovery_admin:admin@{host}:5432/postgres",
                    RECOVERY_ALLOW_DATABASE_RECREATE="I_UNDERSTAND",
                    SOURCE_SHA="a" * 40,
                ),
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("recovery_url_multi_host_not_allowed", completed.stderr)
            self.assertFalse(marker.exists())

    def test_restore_filters_vector_and_public_schema_toc_entries(self) -> None:
        rollback = ROOT / "scripts" / "deploy" / "rollback_release.sh"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = _write_bundle(root)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            filtered_marker = root / "toc-filtered"
            _write_executable(
                fake_bin / "pg_restore",
                "#!/usr/bin/env bash\n"
                "if [[ \"${1:-}\" == \"--list\" ]]; then\n"
                "  printf '; archive\\n"
                "1; 2615 2200 SCHEMA - public pg_database_owner\\n"
                "2; 0 0 COMMENT - SCHEMA public pg_database_owner\\n"
                "3; 3079 100 EXTENSION - vector nexus_recovery_admin\\n"
                "4; 0 0 COMMENT - EXTENSION vector nexus_recovery_admin\\n"
                "5; 1259 101 TABLE public sample nexus_recovery_source\\n'\n"
                "  exit 0\n"
                "fi\n"
                "list=''\n"
                "for arg in \"$@\"; do case \"$arg\" in --use-list=*) list=\"${arg#--use-list=}\" ;; esac; done\n"
                "test -n \"$list\"\n"
                "grep -q '^;1; .* SCHEMA - public ' \"$list\"\n"
                "grep -q '^;2; .* COMMENT - SCHEMA public ' \"$list\"\n"
                "grep -q '^;3; .* EXTENSION - vector ' \"$list\"\n"
                "grep -q '^;4; .* COMMENT - EXTENSION vector ' \"$list\"\n"
                "grep -q '^5; .* TABLE public sample ' \"$list\"\n"
                f"touch {filtered_marker!s}\n"
                "exit 0\n",
            )
            _write_executable(
                fake_bin / "psql",
                "#!/usr/bin/env bash\n"
                "case \"$*\" in\n"
                "  *'SELECT current_database()'*) printf 'nexus_restore\\n' ;;\n"
                "  *\"SELECT extversion FROM pg_extension WHERE extname = 'vector'\"*) printf '0.8.0\\n' ;;\n"
                "  *'WITH extension_owned'*) printf '0|0\\n' ;;\n"
                "  *'SELECT version_num FROM alembic_version'*) printf '20260713_0059\\n' ;;\n"
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
                    POSTGRES_NATIVE_URL="postgresql://nexus_recovery_restore:restore@db-a:5432/nexus_restore",
                    ROLLBACK_STATUS_FILE=str(status),
                ),
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(filtered_marker.exists())
            payload = json.loads(status.read_text(encoding="utf-8"))
            self.assertTrue(payload["database_restored"])

    def test_restore_target_proof_covers_non_relation_public_objects(self) -> None:
        rollback = (ROOT / "scripts" / "deploy" / "rollback_release.sh").read_text(encoding="utf-8")
        for catalog in ("pg_class", "pg_proc", "pg_type", "pg_operator", "pg_collation", "pg_conversion"):
            self.assertIn(catalog, rollback)
        self.assertIn("extension_owned", rollback)
        self.assertIn("rollback_target_not_empty", rollback)

    def test_image_rollback_restarts_runtime_warmer(self) -> None:
        rollback = (ROOT / "scripts" / "deploy" / "rollback_release.sh").read_text(encoding="utf-8")
        self.assertIn("runtime-warmer", rollback)
        self.assertIn("--no-build --pull always", rollback)

    def test_recovery_timeline_rejects_restore_before_backup_completion(self) -> None:
        module = _load_builder()
        snapshot = {
            "schema_version": "nexus_recovery_snapshot_v1",
            "alembic_head": "20260713_0059",
            "table_count": 1,
            "tables": {"markets": 1},
            "foreign_key_signature_count": 0,
            "foreign_key_signatures": [],
            "invalid_foreign_key_count": 0,
            "synthetic_marker_count": 1,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            restored = root / "restored.json"
            output = root / "evidence.json"
            source.write_text(json.dumps(snapshot), encoding="utf-8")
            restored.write_text(json.dumps(snapshot), encoding="utf-8")
            code = module.compare(
                source,
                restored,
                output,
                source_sha="a" * 40,
                backup_sha256="sha256:" + "b" * 64,
                marker_committed_at="2026-07-13T00:00:00+00:00",
                backup_completed_at="2026-07-13T00:00:10+00:00",
                restore_started_at="2026-07-13T00:00:05+00:00",
                restore_completed_at="2026-07-13T00:00:20+00:00",
                rto_target_seconds=120,
                rpo_target_seconds=60,
            )
            evidence = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(code, 1)
        self.assertIn("recovery.restore_started_before_backup_completed", evidence["reasons"])
        self.assertIn("recovery.timeline_order_invalid", evidence["reasons"])


if __name__ == "__main__":
    unittest.main()
