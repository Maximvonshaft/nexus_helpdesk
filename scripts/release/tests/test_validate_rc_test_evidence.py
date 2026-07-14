from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "validate_rc_test_evidence.py"
SPEC = importlib.util.spec_from_file_location("validate_rc_test_evidence", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
REPO_ROOT = Path(__file__).resolve().parents[3]


def _write_preflight_failure(root: Path) -> None:
    (root / "failure-summary.json").write_text(
        json.dumps({
            "schema": "nexus.osr.rc-test-failure-summary.v1",
            "status": "failed",
            "stage": "rc-preflight",
            "exit_code": 1,
            "reason_code": "release_unit_tests_failed",
            "service_states": {},
        }) + "\n",
        encoding="utf-8",
    )
    (root / "preflight-result.json").write_text(
        json.dumps({
            "schema": "nexus.osr.rc-preflight.v1",
            "status": "fail",
            "stage": "release_unit_tests",
            "exit_code": 1,
            "output_sha256": hashlib.sha256(b"bounded failure").hexdigest(),
            "output_bytes": len(b"bounded failure"),
        }) + "\n",
        encoding="utf-8",
    )


class ValidateRcTestEvidenceTests(unittest.TestCase):
    def test_unexpected_file_reports_bounded_filename(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp:
            root = Path(tmp)
            (root / "unexpected-safe-name.txt").write_text("bounded\n", encoding="utf-8")
            with self.assertRaises(MODULE.EvidenceSetError) as context:
                MODULE.validate(root, root / "inputs.txt")
            self.assertEqual(context.exception.reason_code, "unexpected_evidence_files")
            self.assertEqual(context.exception.entries, ["unexpected-safe-name.txt"])

    def test_missing_success_files_are_classified_without_free_text(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp:
            root = Path(tmp)
            (root / "candidate-manifest.json").write_text("{}\n", encoding="utf-8")
            with self.assertRaises(MODULE.EvidenceSetError) as context:
                MODULE.validate(root, root / "inputs.txt")
            self.assertEqual(context.exception.reason_code, "missing_success_evidence_files")
            self.assertIn("browser-smoke.txt", context.exception.entries)

    def test_oversized_evidence_is_rejected_before_scan(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp:
            root = Path(tmp)
            path = root / "browser-smoke.txt"
            path.write_bytes(b"x" * (MODULE.MAX_BYTES + 1))
            with self.assertRaises(MODULE.EvidenceSetError) as context:
                MODULE.validate(root, root / "inputs.txt")
            self.assertEqual(context.exception.reason_code, "evidence_file_too_large")
            self.assertEqual(context.exception.entries, ["browser-smoke.txt"])

    def test_preflight_only_failure_bundle_is_accepted_and_explicitly_scanned(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp:
            root = Path(tmp)
            _write_preflight_failure(root)
            list_output = root / "scan-inputs.txt"

            self.assertEqual(MODULE.validate(root, list_output), 0)

            inputs = list_output.read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                {Path(item).name for item in inputs},
                {"failure-summary.json", "preflight-result.json"},
            )

    def test_preflight_only_failure_requires_fail_status_and_bounded_schema(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp:
            root = Path(tmp)
            _write_preflight_failure(root)
            result_path = root / "preflight-result.json"
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            payload["status"] = "pass"
            result_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

            with self.assertRaises(MODULE.EvidenceSetError) as context:
                MODULE.validate(root, root / "inputs.txt")
            self.assertEqual(context.exception.reason_code, "preflight_result_invalid")

    def test_preflight_failure_summary_rejects_free_text_reason(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp:
            root = Path(tmp)
            _write_preflight_failure(root)
            summary_path = root / "failure-summary.json"
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
            payload["reason_code"] = "raw failure output"
            summary_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

            with self.assertRaises(MODULE.EvidenceSetError) as context:
                MODULE.validate(root, root / "inputs.txt")
            self.assertEqual(context.exception.reason_code, "failure_summary_invalid")


if __name__ == "__main__":
    unittest.main()
