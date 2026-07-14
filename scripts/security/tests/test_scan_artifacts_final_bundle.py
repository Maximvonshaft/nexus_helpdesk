from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SECURITY_ROOT = Path(__file__).resolve().parents[1]
if str(SECURITY_ROOT) not in sys.path:
    sys.path.insert(0, str(SECURITY_ROOT))

from scan_artifacts import _suppress_validated_final_bundle_metadata_findings
from scanner import scan_artifact_files


class ControlledCandidateFinalBundleScanTests(unittest.TestCase):
    source = "d257ef3baf6fca12c662809581a21190559f41e5"
    build_time = "20260714T004020Z"
    generated_at = "2026-07-14T00:45:00Z"
    evaluated_on = "2026-07-14"
    attestation_id = "123456789012"

    def _write_bundle(self, root: Path) -> list[str]:
        bundle = root / "artifacts/final-controlled-candidate"
        bundle.mkdir(parents=True)
        image_tag = f"nexusdesk/helpdesk:rc-test-{self.source}"
        app_version = f"controlled-{self.source[:12]}"
        documents = {
            "candidate-manifest.json": {
                "schema": "nexus.osr.rc-test-candidate.v1",
                "release_class": "controlled_test_deployment",
                "decision": "RC0_TEST_DEPLOYABLE",
                "candidate": {"source_sha": self.source, "image_tag": image_tag},
                "checks": {"example": "pass"},
                "evidence": {},
                "safety": {
                    "production_data_used": False,
                    "production_network_joined": False,
                    "provider_candidate_enabled": False,
                    "real_outbound_enabled": False,
                    "whatsapp_enabled": False,
                    "speedaf_write_enabled": False,
                    "operations_dispatch_enabled": False,
                    "production_ready": False,
                    "full_osr_automation": "NO_GO",
                    "test_environment_isolated": True,
                },
            },
            "registry-publish-receipt.json": {
                "schema": "nexus.osr.registry-publish-receipt.v1",
                "status": "pass",
                "source_sha": self.source,
                "app_version": app_version,
                "build_time": self.build_time,
                "embedded_image_tag": image_tag,
                "image_pushed": True,
                "deployment_performed": False,
            },
            "release-image-compliance-binding.json": {
                "schema_version": "nexus_release_image_compliance_binding_v1",
                "status": "pass",
                "source_sha": self.source,
                "evaluated_on": self.evaluated_on,
                "image_pushed": False,
                "deployment_performed": False,
            },
            "release-image-manifest.json": {
                "schema_version": "nexus_release_image_assurance_v1",
                "status": "pass",
                "source_sha": self.source,
                "critical_count": 0,
                "high_count": 0,
                "unresolved_license_count": 0,
                "image_pushed": False,
                "deployment_performed": False,
            },
            "recovery-evidence.json": {
                "schema_version": "nexus_postgres_recovery_qualification_v1",
                "status": "pass",
                "source_sha": self.source,
                "reasons": [],
                "production_data_used": False,
                "production_mutation_performed": False,
            },
            "controlled-candidate-manifest.json": {
                "schema": "nexus.osr.controlled-candidate-manifest.v1",
                "status": "pass",
                "decision": "CONTROLLED_SERVER_CANDIDATE_PUBLISHED",
                "release_class": "controlled_server_deployment",
                "generated_at": self.generated_at,
                "candidate": {
                    "source_sha": self.source,
                    "app_version": app_version,
                    "build_time": self.build_time,
                    "embedded_image_tag": image_tag,
                },
                "attestation": {
                    "id": self.attestation_id,
                    "url": (
                        "https://github.com/Maximvonshaft/nexus_helpdesk/attestations/"
                        + self.attestation_id
                    ),
                    "registry_provenance_pushed": True,
                },
                "evidence": {},
                "safety": {
                    "production_ready": False,
                    "full_osr_automation": "NO_GO",
                    "issue_533_go": False,
                    "deployment_performed": False,
                    "production_data_used": False,
                    "provider_enabled": False,
                    "real_outbound_enabled": False,
                    "whatsapp_enabled": False,
                    "speedaf_writes_enabled": False,
                    "operations_dispatch_enabled": False,
                    "external_effects_authorized": False,
                },
            },
        }
        for name, payload in documents.items():
            (bundle / name).write_text(json.dumps(payload), encoding="utf-8")
        return [str(path.relative_to(root)) for path in sorted(bundle.glob("*.json"))]

    def test_complete_cross_bound_bundle_suppresses_only_technical_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._write_bundle(root)
            findings = scan_artifact_files(root, paths)
            self.assertEqual(len(findings), 11)
            self.assertEqual(
                {finding.rule for finding in findings},
                {"artifact:phone", "artifact:tracking"},
            )

            remaining, suppressed = _suppress_validated_final_bundle_metadata_findings(
                root=root,
                paths=paths,
                findings=findings,
            )

            self.assertEqual(remaining, [])
            self.assertEqual(suppressed, 11)

    def test_cross_binding_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._write_bundle(root)
            controlled_path = root / "artifacts/final-controlled-candidate/controlled-candidate-manifest.json"
            controlled = json.loads(controlled_path.read_text(encoding="utf-8"))
            controlled["candidate"]["build_time"] = "20260714T004021Z"
            controlled_path.write_text(json.dumps(controlled), encoding="utf-8")
            findings = scan_artifact_files(root, paths)

            remaining, suppressed = _suppress_validated_final_bundle_metadata_findings(
                root=root,
                paths=paths,
                findings=findings,
            )

            self.assertGreater(len(remaining), 0)
            self.assertTrue(any(finding.rule == "artifact:tracking" for finding in remaining))
            self.assertEqual(suppressed, 2)

    def test_unrelated_phone_value_remains_a_finding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._write_bundle(root)
            controlled_path = root / "artifacts/final-controlled-candidate/controlled-candidate-manifest.json"
            controlled = json.loads(controlled_path.read_text(encoding="utf-8"))
            controlled["evidence"]["customer_contact"] = "+382 67 123 456"
            controlled_path.write_text(json.dumps(controlled), encoding="utf-8")
            findings = scan_artifact_files(root, paths)

            remaining, suppressed = _suppress_validated_final_bundle_metadata_findings(
                root=root,
                paths=paths,
                findings=findings,
            )

            self.assertEqual(suppressed, 11)
            self.assertEqual([finding.rule for finding in remaining], ["artifact:phone"])

    def test_secret_rule_is_never_suppressed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._write_bundle(root)
            controlled_path = root / "artifacts/final-controlled-candidate/controlled-candidate-manifest.json"
            controlled = json.loads(controlled_path.read_text(encoding="utf-8"))
            controlled["evidence"]["opaque_value"] = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"
            controlled_path.write_text(json.dumps(controlled), encoding="utf-8")
            findings = scan_artifact_files(root, paths)

            remaining, suppressed = _suppress_validated_final_bundle_metadata_findings(
                root=root,
                paths=paths,
                findings=findings,
            )

            self.assertEqual(suppressed, 11)
            self.assertEqual([finding.rule for finding in remaining], ["artifact:github_token"])


if __name__ == "__main__":
    unittest.main()
