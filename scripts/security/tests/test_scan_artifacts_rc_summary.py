from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SECURITY_DIR = Path(__file__).resolve().parents[1]
if str(SECURITY_DIR) not in sys.path:
    sys.path.insert(0, str(SECURITY_DIR))

MODULE_PATH = SECURITY_DIR / "scan_artifacts.py"
SPEC = importlib.util.spec_from_file_location("scan_artifacts", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

from scanner import Finding


class RcArtifactScanSummaryTests(unittest.TestCase):
    def test_rc_summary_contains_only_bounded_rule_and_path_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "rc-test"
            root.mkdir()
            output = root / "artifact-scan.json"
            findings = [
                Finding(
                    rule="artifact:tracking",
                    path="artifacts/rc-test/readyz.json",
                    line=0,
                    fingerprint="0123456789abcdef",
                )
            ]
            with patch.dict(os.environ, {"RC_EVIDENCE_DIR": str(root)}, clear=False):
                MODULE._write_rc_failure_summary(output, findings)

            payload = json.loads((root / "failure-summary.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["reason_code"], "artifact_scan_findings")
            self.assertEqual(payload["finding_rules"], ["artifact:tracking"])
            self.assertEqual(payload["finding_paths"], ["artifacts/rc-test/readyz.json"])
            self.assertNotIn("fingerprint", payload)
            self.assertNotIn("0123456789abcdef", json.dumps(payload))

    def test_non_rc_output_does_not_create_companion_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "not-rc"
            root.mkdir()
            output = root / "artifact-scan.json"
            findings = [Finding("artifact:phone", "report.json", 0, "0123456789abcdef")]
            with patch.dict(os.environ, {"RC_EVIDENCE_DIR": str(root)}, clear=False):
                MODULE._write_rc_failure_summary(output, findings)
            self.assertFalse((root / "failure-summary.json").exists())


if __name__ == "__main__":
    unittest.main()
