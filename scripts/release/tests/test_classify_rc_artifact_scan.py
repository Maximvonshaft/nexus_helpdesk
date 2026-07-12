from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "classify_rc_artifact_scan.py"
SPEC = importlib.util.spec_from_file_location("classify_rc_artifact_scan", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ClassifyRcArtifactScanTests(unittest.TestCase):
    def test_bounded_findings_are_preserved_without_raw_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "artifact-scan.json"
            summary = root / "failure-summary.json"
            report.write_text(
                json.dumps(
                    {
                        "schema_version": "nexus_security_artifact_scan_v1",
                        "status": "fail",
                        "scanned_files": 24,
                        "finding_count": 2,
                        "findings": [
                            {
                                "rule": "artifact:tracking",
                                "path": "artifacts/rc-test/http-core-smoke.json",
                                "line": 0,
                                "fingerprint": "0123456789abcdef",
                            },
                            {
                                "rule": "artifact:phone",
                                "path": "artifacts/rc-test/network-safety.json",
                                "line": 0,
                                "fingerprint": "fedcba9876543210",
                            },
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            payload = MODULE.classify(report, summary, 1)

            self.assertEqual(payload["reason_code"], "artifact_scan_findings")
            self.assertEqual(payload["scan_finding_count"], 2)
            self.assertEqual(payload["scan_scanned_files"], 24)
            self.assertEqual(len(payload["scan_findings"]), 2)
            self.assertNotIn("value", json.dumps(payload))

    def test_invalid_or_missing_report_remains_generic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = MODULE.classify(root / "missing.json", root / "summary.json", 2)
            self.assertEqual(payload["reason_code"], "evidence_validation_or_scan_failed")
            self.assertNotIn("scan_findings", payload)

    def test_unsafe_finding_metadata_is_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "artifact-scan.json"
            summary = root / "summary.json"
            report.write_text(
                json.dumps(
                    {
                        "schema_version": "nexus_security_artifact_scan_v1",
                        "finding_count": 1,
                        "scanned_files": 1,
                        "findings": [
                            {
                                "rule": "artifact:tracking",
                                "path": "../outside.json",
                                "fingerprint": "not-safe",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            payload = MODULE.classify(report, summary, 1)
            self.assertEqual(payload["reason_code"], "artifact_scan_findings")
            self.assertNotIn("scan_findings", payload)


if __name__ == "__main__":
    unittest.main()
