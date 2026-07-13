from __future__ import annotations

import importlib.util
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "generate_rc_test_env.py"
SPEC = importlib.util.spec_from_file_location("generate_rc_test_env", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class GenerateRcTestEnvTests(unittest.TestCase):
    def _write_revision(self, root: Path, name: str, *, revision: str, down_revision) -> None:
        path = root / name
        path.write_text(
            textwrap.dedent(
                f'''\
                revision = {revision!r}
                down_revision = {down_revision!r}
                '''
            ),
            encoding="utf-8",
        )

    def test_discovers_one_exact_alembic_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            versions = Path(tmp)
            self._write_revision(versions, "a.py", revision="0001", down_revision=None)
            self._write_revision(versions, "b.py", revision="0002", down_revision="0001")
            self._write_revision(versions, "c.py", revision="0003", down_revision=("0002",))

            self.assertEqual(MODULE.discover_alembic_head(versions), "0003")

    def test_rejects_multiple_or_malformed_alembic_heads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            versions = Path(tmp)
            self._write_revision(versions, "a.py", revision="0001", down_revision=None)
            self._write_revision(versions, "b.py", revision="0002", down_revision=None)
            with self.assertRaisesRegex(ValueError, "alembic_head_count_invalid"):
                MODULE.discover_alembic_head(versions)

        with tempfile.TemporaryDirectory() as tmp:
            versions = Path(tmp)
            (versions / "bad.py").write_text("revision = dynamic_value\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "alembic_revision_invalid"):
                MODULE.discover_alembic_head(versions)

    def test_rejects_disconnected_cycle_even_when_one_valid_head_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            versions = Path(tmp)
            self._write_revision(versions, "a.py", revision="0001", down_revision=None)
            self._write_revision(versions, "b.py", revision="0002", down_revision="0001")
            self._write_revision(versions, "c.py", revision="cycle_c", down_revision="cycle_d")
            self._write_revision(versions, "d.py", revision="cycle_d", down_revision="cycle_c")

            with self.assertRaisesRegex(ValueError, "alembic_graph_unreachable"):
                MODULE.discover_alembic_head(versions)

    def test_generated_environment_is_shell_loadable_and_fail_closed(self) -> None:
        values = MODULE.build_values(
            source_sha="a" * 40,
            compose_project="nexus_rc_test_123",
            origin="http://127.0.0.1:18083/",
            expected_migration_head="20260711_0058",
        )
        self.assertEqual(values["RC_BASE_URL"], "http://127.0.0.1:18083")
        self.assertEqual(values["RC_PUBLIC_ORIGIN"], values["RC_BASE_URL"])
        self.assertEqual(values["RC_TEST_DISPLAY_NAME"], "RC-Test-Website")
        self.assertEqual(values["EXPECTED_MIGRATION_HEAD"], "20260711_0058")
        self.assertEqual(values["READINESS_REQUIRE_RELEASE_METADATA"], "true")
        self.assertEqual(values["TENANT_RUNTIME_AUTHORITY_MODE"], "enforce")
        self.assertEqual(values["PROVIDER_RUNTIME_KILL_SWITCH"], "true")
        self.assertEqual(values["PROVIDER_RUNTIME_CANARY_PERCENT"], "0")
        self.assertEqual(values["ENABLE_OUTBOUND_DISPATCH"], "false")
        self.assertEqual(values["OPERATIONS_DISPATCH_MODE"], "disabled")
        self.assertTrue(all(not any(char.isspace() for char in value) for value in values.values()))

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env.rc-test"
            MODULE.write_env(path, values)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            result = subprocess.run(
                ["bash", "-n", str(path)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            loaded = subprocess.run(
                [
                    "bash",
                    "-c",
                    f"set -a; source {path}; set +a; printf '%s|%s|%s|%s' \"$GIT_SHA\" \"$RC_PUBLIC_ORIGIN\" \"$RC_TEST_DISPLAY_NAME\" \"$EXPECTED_MIGRATION_HEAD\"",
                ],
                text=True,
                capture_output=True,
                check=False,
                env={"PATH": os.environ.get("PATH", "")},
            )
            self.assertEqual(loaded.returncode, 0, loaded.stderr)
            self.assertEqual(
                loaded.stdout,
                "a" * 40 + "|http://127.0.0.1:18083|RC-Test-Website|20260711_0058",
            )

    def test_rejects_remote_insecure_http_and_invalid_identity(self) -> None:
        with self.assertRaises(ValueError):
            MODULE.build_values(
                source_sha="not-a-sha",
                compose_project="nexus_rc",
                origin="http://127.0.0.1:18083",
                expected_migration_head="0001",
            )
        with self.assertRaises(ValueError):
            MODULE.build_values(
                source_sha="a" * 40,
                compose_project="nexus_rc",
                origin="http://example.com",
                expected_migration_head="0001",
            )
        with self.assertRaises(ValueError):
            MODULE.build_values(
                source_sha="a" * 40,
                compose_project="bad project",
                origin="http://127.0.0.1:18083",
                expected_migration_head="0001",
            )
        with self.assertRaisesRegex(ValueError, "expected migration head"):
            MODULE.build_values(
                source_sha="a" * 40,
                compose_project="nexus_rc",
                origin="http://127.0.0.1:18083",
                expected_migration_head="bad head",
            )

    def test_write_rejects_unquoted_whitespace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                MODULE.write_env(Path(tmp) / "bad.env", {"BAD": "two words"})


if __name__ == "__main__":
    unittest.main()
