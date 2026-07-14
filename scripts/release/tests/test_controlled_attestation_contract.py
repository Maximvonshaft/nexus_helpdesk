from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = (ROOT / ".github/workflows/controlled-candidate-convergence.yml").read_text(
    encoding="utf-8"
)


class ControlledAttestationContractTests(unittest.TestCase):
    def test_personal_repository_attestation_skips_unsupported_storage_record(self) -> None:
        attest_block = WORKFLOW.split("- name: Attest exact registry digest", 1)[1].split(
            "- name: Build final evidence-bound candidate", 1
        )[0]

        self.assertIn(
            "actions/attest-build-provenance@0f67c3f4856b2e3261c31976d6725780e5e4c373",
            attest_block,
        )
        self.assertIn("subject-name: ${{ steps.identity.outputs.image }}", attest_block)
        self.assertIn("subject-digest: ${{ steps.identity.outputs.digest }}", attest_block)
        self.assertIn("push-to-registry: true", attest_block)
        self.assertIn("create-storage-record: false", attest_block)


if __name__ == "__main__":
    unittest.main()
