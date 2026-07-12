from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RETIRED_DELIVERY_ARTIFACTS = (
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
    "docs/round-b-delivery-report.md",
    "docs/round-b-self-audit.md",
    "docs/round-b-readonly-audit.md",
    "docs/round-b-implementation-plan.md",
    "docs/round-b-operator-demo-script.md",
    "docs/round-b-post-push-audit.md",
)


class RetiredDeliveryArtifactContractTests(unittest.TestCase):
    def test_retired_delivery_artifacts_are_absent(self) -> None:
        returned = [path for path in RETIRED_DELIVERY_ARTIFACTS if (ROOT / path).exists()]
        self.assertEqual(returned, [], f"retired delivery artifacts returned: {returned}")

    def test_historical_evidence_index_exists(self) -> None:
        index = ROOT / "docs" / "governance" / "historical-delivery-evidence.md"
        self.assertTrue(index.is_file())
        text = index.read_text(encoding="utf-8")
        for path in RETIRED_DELIVERY_ARTIFACTS:
            self.assertIn(f"`{path}`", text)
        self.assertIn("git show <commit>:<path>", text)
        self.assertIn("#652", text)
        self.assertIn("#656", text)


if __name__ == "__main__":
    unittest.main()
