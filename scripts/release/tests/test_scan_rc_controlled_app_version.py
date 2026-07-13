from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scan_rc_test_artifacts.py"
SPEC = importlib.util.spec_from_file_location("scan_rc_test_artifacts", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ControlledAppVersionScannerTests(unittest.TestCase):
    def _scan(self, app_version: str):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = "artifacts/rc-test/healthz.json"
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"app_version": app_version}) + "\n", encoding="utf-8")
            return MODULE.scan_rc_artifact_files(root, [relative])

    def test_exact_controlled_app_version_is_safe_technical_metadata(self) -> None:
        findings, suppressed = self._scan("controlled-d3997cb2453f")
        self.assertEqual(findings, [])
        self.assertGreater(suppressed, 0)

    def test_malformed_controlled_app_version_is_not_suppressed(self) -> None:
        findings, suppressed = self._scan("controlled-D3997CB2453F")
        self.assertEqual(suppressed, 0)
        self.assertIn("artifact:tracking", {finding.rule for finding in findings})

    def test_secret_detection_is_unchanged(self) -> None:
        findings, suppressed = self._scan("sk-proj-" + "A" * 36)
        self.assertEqual(suppressed, 0)
        self.assertIn("artifact:openai_key", {finding.rule for finding in findings})


if __name__ == "__main__":
    unittest.main()
