from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FINALIZER = (ROOT / "scripts/release/finalize_controlled_candidate.sh").read_text(encoding="utf-8")


class FinalizeControlledCandidateContractTests(unittest.TestCase):
    def test_uses_dedicated_fail_closed_final_artifact_scanner(self) -> None:
        self.assertIn("scripts/release/scan_controlled_candidate_artifacts.py", FINALIZER)
        self.assertNotIn("scripts/security/scan_artifacts.py", FINALIZER)
        self.assertIn('"${FINAL_DIR}"/*.json', FINALIZER)
        self.assertLess(
            FINALIZER.index("build_controlled_candidate_manifest.py"),
            FINALIZER.index("scan_controlled_candidate_artifacts.py"),
        )
        self.assertLess(
            FINALIZER.index("scan_controlled_candidate_artifacts.py"),
            FINALIZER.index(".safety.production_ready"),
        )


if __name__ == "__main__":
    unittest.main()
