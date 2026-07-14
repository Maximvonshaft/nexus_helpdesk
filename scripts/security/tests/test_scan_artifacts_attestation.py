from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SECURITY_ROOT = Path(__file__).resolve().parents[1]
if str(SECURITY_ROOT) not in sys.path:
    sys.path.insert(0, str(SECURITY_ROOT))

from scan_artifacts import _suppress_validated_attestation_phone_findings
from scanner import scan_artifact_files


class ControlledCandidateAttestationScanTests(unittest.TestCase):
    attestation_id = "123456789012"

    def _write_manifest(self, root: Path, *, url_id: str | None = None, schema: str | None = None) -> str:
        relative = "controlled-candidate-manifest.json"
        payload = {
            "schema": schema or "nexus.osr.controlled-candidate-manifest.v1",
            "status": "pass",
            "attestation": {
                "id": self.attestation_id,
                "url": (
                    "https://github.com/Maximvonshaft/nexus_helpdesk/attestations/"
                    + (url_id or self.attestation_id)
                ),
                "registry_provenance_pushed": True,
            },
        }
        (root / relative).write_text(json.dumps(payload), encoding="utf-8")
        return relative

    def test_exact_validated_attestation_phone_findings_are_suppressed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = self._write_manifest(root)
            findings = scan_artifact_files(root, [relative])
            self.assertEqual([finding.rule for finding in findings], ["artifact:phone", "artifact:phone"])

            remaining, suppressed = _suppress_validated_attestation_phone_findings(
                root=root,
                paths=[relative],
                findings=findings,
            )

            self.assertEqual(remaining, [])
            self.assertEqual(suppressed, 2)

    def test_mismatched_attestation_url_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = self._write_manifest(root, url_id="999999999999")
            findings = scan_artifact_files(root, [relative])

            remaining, suppressed = _suppress_validated_attestation_phone_findings(
                root=root,
                paths=[relative],
                findings=findings,
            )

            self.assertEqual(remaining, findings)
            self.assertEqual(suppressed, 0)

    def test_non_candidate_schema_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = self._write_manifest(root, schema="untrusted.v1")
            findings = scan_artifact_files(root, [relative])

            remaining, suppressed = _suppress_validated_attestation_phone_findings(
                root=root,
                paths=[relative],
                findings=findings,
            )

            self.assertEqual(remaining, findings)
            self.assertEqual(suppressed, 0)

    def test_unrelated_sensitive_values_are_never_suppressed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = self._write_manifest(root)
            path = root / relative
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["customer_phone"] = "+382 67 123 456"
            path.write_text(json.dumps(payload), encoding="utf-8")
            findings = scan_artifact_files(root, [relative])

            remaining, suppressed = _suppress_validated_attestation_phone_findings(
                root=root,
                paths=[relative],
                findings=findings,
            )

            self.assertEqual(suppressed, 2)
            self.assertEqual([finding.rule for finding in remaining], ["artifact:phone"])


if __name__ == "__main__":
    unittest.main()
