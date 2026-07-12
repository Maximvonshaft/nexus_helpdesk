from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "build_rc_test_manifest.py"
SPEC = importlib.util.spec_from_file_location("build_rc_test_manifest", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class BuildRcTestManifestTests(unittest.TestCase):
    def _write_rollback(self, root: Path, **overrides: object) -> None:
        payload = {
            "schema": "nexus.osr.rc-test-rollback-verification.v1",
            "status": "pass",
            "remaining_containers": 0,
            "remaining_volumes": 0,
            "remaining_networks": 0,
            **overrides,
        }
        (root / "rollback-verification.json").write_text(
            json.dumps(payload) + "\n",
            encoding="utf-8",
        )

    def test_empty_teardown_transcript_is_normalized_only_after_zero_resource_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "teardown.txt").write_text("", encoding="utf-8")
            self._write_rollback(root)

            MODULE._finalize_teardown_evidence(root)

            self.assertEqual(
                (root / "teardown.txt").read_text(encoding="utf-8"),
                "RC_TEARDOWN_COMPLETED=true\n"
                "remaining_containers=0\n"
                "remaining_volumes=0\n"
                "remaining_networks=0\n",
            )

    def test_remaining_resource_fails_closed_and_does_not_create_pass_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "teardown.txt").write_text("", encoding="utf-8")
            self._write_rollback(root, remaining_volumes=1)

            with self.assertRaisesRegex(ValueError, "remaining resources"):
                MODULE._finalize_teardown_evidence(root)

            self.assertEqual((root / "teardown.txt").read_text(encoding="utf-8"), "")

    def test_nonempty_command_transcript_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transcript = root / "teardown.txt"
            transcript.write_text("container removed\n", encoding="utf-8")

            MODULE._finalize_teardown_evidence(root)

            self.assertEqual(transcript.read_text(encoding="utf-8"), "container removed\n")


if __name__ == "__main__":
    unittest.main()
