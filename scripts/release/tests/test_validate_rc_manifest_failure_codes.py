from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "validate_rc_test_manifest.py"
SPEC = importlib.util.spec_from_file_location("validate_rc_test_manifest_codes", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ManifestFailureCodeTests(unittest.TestCase):
    def test_evidence_failure_codes_are_bounded_and_specific(self) -> None:
        cases = {
            "evidence.migration size is outside the bounded range": "evidence_migration_size_invalid",
            "evidence.teardown.size_bytes mismatch": "evidence_teardown_size_mismatch",
            "evidence.browser_smoke.sha256 mismatch": "evidence_browser_smoke_digest_mismatch",
            "evidence.readiness.path must not be a symlink": "evidence_readiness_path_invalid",
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertEqual(MODULE._reason_code(MODULE.ManifestError(message)), expected)

    def test_candidate_and_contract_failures_are_specific(self) -> None:
        cases = {
            "candidate.image_id must use exact sha256:<64 hex> form": "candidate_image_id_invalid",
            "required checks are not pass: browser_smoke": "checks_not_pass",
            "safety.full_osr_automation must remain NO_GO": "safety_contract_invalid",
            "missing evidence: teardown": "missing_evidence",
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertEqual(MODULE._reason_code(MODULE.ManifestError(message)), expected)


if __name__ == "__main__":
    unittest.main()
