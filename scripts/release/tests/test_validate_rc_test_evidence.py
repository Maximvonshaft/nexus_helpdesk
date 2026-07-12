from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "validate_rc_test_evidence.py"
SPEC = importlib.util.spec_from_file_location("validate_rc_test_evidence", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
REPO_ROOT = Path(__file__).resolve().parents[3]


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


if __name__ == "__main__":
    unittest.main()
