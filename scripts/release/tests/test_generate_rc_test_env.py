from __future__ import annotations

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "generate_rc_test_env.py"
SPEC = importlib.util.spec_from_file_location("generate_rc_test_env", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class GenerateRcTestEnvTests(unittest.TestCase):
    def test_generated_environment_is_shell_loadable_and_fail_closed(self) -> None:
        values = MODULE.build_values(
            source_sha="a" * 40,
            compose_project="nexus_rc_test_123",
            origin="http://127.0.0.1:18083/",
        )
        self.assertEqual(values["RC_BASE_URL"], "http://127.0.0.1:18083")
        self.assertEqual(values["RC_PUBLIC_ORIGIN"], values["RC_BASE_URL"])
        self.assertEqual(values["RC_TEST_DISPLAY_NAME"], "RC-Test-Website")
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
                [
                    "bash",
                    "-n",
                    str(path),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            loaded = subprocess.run(
                [
                    "bash",
                    "-c",
                    f"set -a; source {path}; set +a; printf '%s|%s|%s' \"$GIT_SHA\" \"$RC_PUBLIC_ORIGIN\" \"$RC_TEST_DISPLAY_NAME\"",
                ],
                text=True,
                capture_output=True,
                check=False,
                env={"PATH": os.environ.get("PATH", "")},
            )
            self.assertEqual(loaded.returncode, 0, loaded.stderr)
            self.assertEqual(
                loaded.stdout,
                "a" * 40 + "|http://127.0.0.1:18083|RC-Test-Website",
            )

    def test_rejects_remote_insecure_http_and_invalid_identity(self) -> None:
        with self.assertRaises(ValueError):
            MODULE.build_values(
                source_sha="not-a-sha",
                compose_project="nexus_rc",
                origin="http://127.0.0.1:18083",
            )
        with self.assertRaises(ValueError):
            MODULE.build_values(
                source_sha="a" * 40,
                compose_project="nexus_rc",
                origin="http://example.com",
            )
        with self.assertRaises(ValueError):
            MODULE.build_values(
                source_sha="a" * 40,
                compose_project="bad project",
                origin="http://127.0.0.1:18083",
            )

    def test_write_rejects_unquoted_whitespace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                MODULE.write_env(Path(tmp) / "bad.env", {"BAD": "two words"})


if __name__ == "__main__":
    unittest.main()
