from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RETIRED_ROOT_REPORTS = (
    "ROUND_A_VERIFY_RESULTS.md",
    "ROUND_A_DELIVERY_REPORT.md",
    "ROUND_B_VERIFY_RESULTS.md",
    "ROUND_B_MOBILE_APPLY.md",
    "ROUND24_HARDENING_REPORT.md",
    "ROUND25_HARDENING_REPORT.md",
    "NEXT_PHASE_MAX_PUSH_REPORT.md",
    "PRODUCTION_HARDENING_FIX_REPORT.md",
    "PRODUCTION_SIGNOFF_REPORT.md",
    "PATCH_NOTES.md",
)


class RetiredRootReportContractTests(unittest.TestCase):
    def test_retired_root_reports_are_absent(self) -> None:
        returned = [path for path in RETIRED_ROOT_REPORTS if (ROOT / path).exists()]
        self.assertEqual(returned, [], f"retired root reports returned: {returned}")

    def test_historical_evidence_index_exists(self) -> None:
        index = ROOT / "docs" / "governance" / "historical-delivery-evidence.md"
        self.assertTrue(index.is_file())
        text = index.read_text(encoding="utf-8")
        for path in RETIRED_ROOT_REPORTS:
            self.assertIn(f"`{path}`", text)
        self.assertIn("git show <commit>:<path>", text)
        self.assertIn("#652", text)


if __name__ == "__main__":
    unittest.main()
