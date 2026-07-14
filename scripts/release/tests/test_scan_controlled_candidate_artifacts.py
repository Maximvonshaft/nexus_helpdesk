from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scan_controlled_candidate_artifacts.py"
SPEC = importlib.util.spec_from_file_location("scan_controlled_candidate_artifacts", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ControlledCandidateArtifactScannerTests(unittest.TestCase):
    source = "a" * 40
    image_id = "sha256:" + "b" * 64
    registry_digest = "sha256:" + "c" * 64
    image_tag = "nexusdesk/helpdesk:rc-test-" + source
    registry_image = "ghcr.io/maximvonshaft/nexus_helpdesk"

    def _write(self, root: Path, name: str, payload: dict) -> None:
        (root / name).write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")

    def _digest(self, path: Path) -> str:
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()

    def _fixture(self, root: Path, *, attestation_id: str = "1234567890") -> None:
        self._write(
            root,
            "candidate-manifest.json",
            {
                "schema": "nexus.osr.rc-test-candidate.v1",
                "candidate": {"image_tag": self.image_tag},
            },
        )
        self._write(
            root,
            "release-image-manifest.json",
            {"schema_version": "nexus_release_image_assurance_v1", "status": "pass"},
        )
        self._write(
            root,
            "release-image-compliance-binding.json",
            {
                "schema_version": "nexus_release_image_compliance_binding_v1",
                "status": "pass",
                "evaluated_on": "2026-07-14",
            },
        )
        self._write(
            root,
            "registry-publish-receipt.json",
            {
                "schema": "nexus.osr.registry-publish-receipt.v1",
                "status": "pass",
                "app_version": "controlled-aaaaaaaaaaaa",
                "build_time": "20260714T001928Z",
                "embedded_image_tag": self.image_tag,
            },
        )
        self._write(
            root,
            "recovery-evidence.json",
            {"schema_version": "nexus_postgres_recovery_qualification_v1", "status": "pass"},
        )

        evidence_files = {
            "rc_candidate_manifest": "candidate-manifest.json",
            "release_image_manifest": "release-image-manifest.json",
            "release_image_compliance_binding": "release-image-compliance-binding.json",
            "recovery_evidence": "recovery-evidence.json",
            "registry_publish_receipt": "registry-publish-receipt.json",
        }
        evidence = {
            key: {"path": name, "sha256": self._digest(root / name)}
            for key, name in evidence_files.items()
        }
        self._write(
            root,
            "controlled-candidate-manifest.json",
            {
                "schema": "nexus.osr.controlled-candidate-manifest.v1",
                "status": "pass",
                "decision": "CONTROLLED_SERVER_CANDIDATE_PUBLISHED",
                "release_class": "controlled_server_deployment",
                "generated_at": "2026-07-14T00:22:49.123456Z",
                "candidate": {
                    "source_sha": self.source,
                    "frontend_build_sha": self.source,
                    "migration_revision": "20260713_0059",
                    "build_time": "20260714T001928Z",
                    "app_version": "controlled-aaaaaaaaaaaa",
                    "embedded_image_tag": self.image_tag,
                    "local_image_id": self.image_id,
                    "registry_pull_image_id": self.image_id,
                    "registry_image": self.registry_image,
                    "registry_digest": self.registry_digest,
                    "registry_reference": f"{self.registry_image}@{self.registry_digest}",
                    "config_profile": "rc-test-isolated-v1",
                    "config_digest": "sha256:" + "d" * 64,
                    "postgres_image_digest": "pgvector/pgvector@sha256:" + "e" * 64,
                    "nginx_image_digest": "nginx@sha256:" + "f" * 64,
                },
                "attestation": {
                    "id": attestation_id,
                    "url": (
                        "https://github.com/Maximvonshaft/nexus_helpdesk/attestations/"
                        + attestation_id
                    ),
                    "registry_provenance_pushed": True,
                },
                "evidence": evidence,
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
        )

    def _scan(self, root: Path) -> tuple[int, dict]:
        output = root / "artifact-scan.json"
        code = MODULE.scan_controlled_candidate_artifacts(root, output)
        return code, json.loads(output.read_text(encoding="utf-8"))

    def test_accepts_valid_bound_machine_metadata_that_matches_generic_pii_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._fixture(root, attestation_id="1234567890")
            code, report = self._scan(root)
            self.assertEqual(code, 0)
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["finding_count"], 0)
            self.assertEqual(report["scanned_files"], 6)

    def test_unknown_sensitive_field_remains_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._fixture(root)
            receipt_path = root / "registry-publish-receipt.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["operator_contact"] = "person@example.com"
            self._write(root, receipt_path.name, receipt)
            final_path = root / "controlled-candidate-manifest.json"
            final = json.loads(final_path.read_text(encoding="utf-8"))
            final["evidence"]["registry_publish_receipt"]["sha256"] = self._digest(receipt_path)
            self._write(root, final_path.name, final)

            code, report = self._scan(root)
            self.assertEqual(code, 1)
            self.assertEqual(report["status"], "fail")
            self.assertIn("artifact:email", report["by_rule"])

    def test_rejects_attestation_url_not_bound_to_exact_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._fixture(root, attestation_id="1234567890")
            final_path = root / "controlled-candidate-manifest.json"
            final = json.loads(final_path.read_text(encoding="utf-8"))
            final["attestation"]["url"] = (
                "https://github.com/Maximvonshaft/nexus_helpdesk/attestations/9999999999"
            )
            self._write(root, final_path.name, final)

            code, report = self._scan(root)
            self.assertEqual(code, 1)
            self.assertIn("controlled_candidate_manifest_invalid", report["by_rule"])

    def test_rejects_unexpected_json_in_final_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._fixture(root)
            self._write(root, "unexpected.json", {"status": "pass"})
            code, report = self._scan(root)
            self.assertEqual(code, 1)
            self.assertIn("controlled_candidate_file_set_invalid", report["by_rule"])


if __name__ == "__main__":
    unittest.main()
