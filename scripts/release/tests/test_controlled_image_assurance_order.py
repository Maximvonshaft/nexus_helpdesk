from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = (ROOT / "scripts/release/run_controlled_image_assurance.sh").read_text(encoding="utf-8")


class ControlledImageAssuranceOrderTests(unittest.TestCase):
    def test_cleanup_and_scan_markers_precede_structured_validation(self) -> None:
        cleanup_marker = 'printf \'%s\\n\' "${cleanup_code}" > "${RELEASE_IMAGE_DIR}/raw-cleanup-exit-code"'
        scan_marker = 'printf \'%s\\n\' "${artifact_scan_code}" > "${RELEASE_IMAGE_DIR}/artifact-scan-exit-code"'
        validation = "python scripts/security/validate_release_image_evidence.py"

        self.assertIn(cleanup_marker, SCRIPT)
        self.assertIn(scan_marker, SCRIPT)
        self.assertIn(validation, SCRIPT)
        self.assertLess(SCRIPT.index(cleanup_marker), SCRIPT.index(validation))
        self.assertLess(SCRIPT.index(scan_marker), SCRIPT.index(validation))

    def test_raw_evidence_is_removed_before_structured_validation(self) -> None:
        validation_index = SCRIPT.index("python scripts/security/validate_release_image_evidence.py")
        cleanup_block = SCRIPT[:validation_index]
        for raw_name in (
            "trivy.raw.json",
            "image.raw.cdx.json",
            "frontend.raw.cdx.json",
            "image.preliminary.cdx.json",
            "image.preliminary.cdx.json.summary.json",
        ):
            self.assertIn(raw_name, cleanup_block)
        self.assertIn('test "${cleanup_code}" = "0"', cleanup_block)

    def test_bounded_artifact_scan_precedes_structured_validation(self) -> None:
        scan_index = SCRIPT.index("python scripts/security/scan_artifacts.py")
        validation_index = SCRIPT.index("python scripts/security/validate_release_image_evidence.py")
        self.assertLess(scan_index, validation_index)
        self.assertIn('"${RELEASE_IMAGE_DIR}/runtime-smoke-summary.txt"', SCRIPT)
        self.assertIn("THIRD_PARTY_NOTICES.md", SCRIPT)
        self.assertIn('test "$(jq -r \' .status\' ', SCRIPT.replace("'.status'", "' .status'"))

    def test_structure_pass_is_required_after_validation(self) -> None:
        validation_index = SCRIPT.index("python scripts/security/validate_release_image_evidence.py")
        required = 'test "$(jq -r \'.status\' "${RELEASE_IMAGE_DIR}/structured-evidence-scan.json")" = "pass"'
        self.assertIn(required, SCRIPT)
        self.assertLess(validation_index, SCRIPT.index(required))


if __name__ == "__main__":
    unittest.main()
