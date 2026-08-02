from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scan_controlled_candidate_artifacts.py"
SPEC = importlib.util.spec_from_file_location("scan_controlled_candidate_artifacts", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ControlledCandidateArtifactScannerTests(unittest.TestCase):
    source = "f2aa9f0346abf6465551de507b0b8a2dfc3be6bc"
    final_prefix = Path("artifacts/final-controlled-candidate")
    canonical_run_id = 30760307852
    expected_repository = "Maximvonshaft/nexus_helpdesk"

    def setUp(self) -> None:
        self.previous_repository = os.environ.get("GITHUB_REPOSITORY")
        os.environ["GITHUB_REPOSITORY"] = self.expected_repository

    def tearDown(self) -> None:
        if self.previous_repository is None:
            os.environ.pop("GITHUB_REPOSITORY", None)
        else:
            os.environ["GITHUB_REPOSITORY"] = self.previous_repository

    def _write(self, root: Path, name: str, payload: dict) -> str:
        path = root / self.final_prefix / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path.relative_to(root).as_posix()

    def _fixtures(self, root: Path) -> list[str]:
        attestation_id = "123456789"
        sidecar_attestation_id = "987654321"
        final = {
            "schema": "nexus.osr.controlled-candidate-manifest.v1",
            "status": "pass",
            "decision": "CONTROLLED_SERVER_CANDIDATE_PUBLISHED",
            "generated_at": "2026-07-14T00:22:58.123456Z",
            "candidate": {
                "source_sha": self.source,
                "migration_revision": "20260729_r15_tenant_scope",
                "build_time": "20260714T001928Z",
                "app_version": "controlled-3bc06eba81db",
                "embedded_image_tag": "nexusdesk/helpdesk:rc-test-" + self.source,
            },
            "attestation": {
                "id": attestation_id,
                "url": (
                    "https://github.com/Maximvonshaft/nexus_helpdesk/attestations/"
                    + attestation_id
                ),
                "registry_provenance_pushed": True,
            },
            "whatsapp_sidecar_attestation": {
                "id": sidecar_attestation_id,
                "url": (
                    "https://github.com/Maximvonshaft/nexus_helpdesk/attestations/"
                    + sidecar_attestation_id
                ),
                "registry_provenance_pushed": True,
            },
        }
        rc = {
            "schema": "nexus.osr.rc-test-candidate.v1",
            "decision": "RC0_TEST_DEPLOYABLE",
            "candidate": {
                "source_sha": self.source,
                "migration_revision": "20260729_r15_tenant_scope",
                "image_tag": "nexusdesk/helpdesk:rc-test-" + self.source,
            },
        }
        binding = {
            "schema_version": "nexus_release_image_compliance_binding_v1",
            "status": "pass",
            "evaluated_on": "2026-07-14",
        }
        receipt = {
            "schema": "nexus.osr.registry-publish-receipt.v1",
            "status": "pass",
            "source_sha": self.source,
            "build_time": "20260714T001928Z",
            "app_version": "controlled-3bc06eba81db",
            "embedded_image_tag": "nexusdesk/helpdesk:rc-test-" + self.source,
        }
        sidecar = {
            "schema": "nexus.whatsapp-sidecar.image-assurance.v1",
            "status": "pass",
            "source_sha": self.source,
            "revision": self.source,
            "image_tag": "nexusdesk/whatsapp-sidecar:controlled-" + self.source,
            "build_time": "2026-07-14T00:19:28Z",
            "app_version": "controlled-" + self.source,
        }
        sidecar_receipt = {
            "schema": "nexus.whatsapp-sidecar.registry-publish-receipt.v1",
            "status": "pass",
            "source_sha": self.source,
            "build_time": "2026-07-14T00:19:28Z",
            "app_version": "controlled-" + self.source,
        }
        canonical_receipt = {
            "schema": "nexus.canonical-acceptance-receipt.v1",
            "workflow": "Canonical Acceptance",
            "event": "push",
            "conclusion": "success",
            "source_sha": self.source,
            "run_id": self.canonical_run_id,
            "run_url": (
                "https://github.com/Maximvonshaft/nexus_helpdesk/actions/runs/"
                + str(self.canonical_run_id)
            ),
        }
        final["candidate"]["whatsapp_sidecar"] = {
            "app_version": "controlled-" + self.source,
        }
        return [
            self._write(root, "controlled-candidate-manifest.json", final),
            self._write(root, "candidate-manifest.json", rc),
            self._write(root, "release-image-compliance-binding.json", binding),
            self._write(root, "registry-publish-receipt.json", receipt),
            self._write(root, "sidecar-image-manifest.json", sidecar),
            self._write(
                root,
                "whatsapp-sidecar-registry-publish-receipt.json",
                sidecar_receipt,
            ),
            self._write(root, "canonical-acceptance-receipt.json", canonical_receipt),
        ]

    def test_suppresses_only_validated_release_metadata_false_positives(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._fixtures(root)
            findings, suppressed = MODULE.scan_controlled_candidate_files(root, paths)
            self.assertEqual(findings, [])
            self.assertGreaterEqual(suppressed, 11)

    def test_secret_finding_is_never_suppressed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._fixtures(root)
            final_path = root / paths[0]
            payload = json.loads(final_path.read_text(encoding="utf-8"))
            payload["unsafe_note"] = "ghp_" + "A" * 40
            final_path.write_text(json.dumps(payload), encoding="utf-8")
            findings, _ = MODULE.scan_controlled_candidate_files(root, paths)
            self.assertIn("artifact:github_token", {finding.rule for finding in findings})

    def test_unbound_attestation_url_is_not_suppressed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._fixtures(root)
            final_path = root / paths[0]
            payload = json.loads(final_path.read_text(encoding="utf-8"))
            payload["attestation"]["url"] = (
                "https://github.com/Maximvonshaft/nexus_helpdesk/attestations/12025550123"
            )
            final_path.write_text(json.dumps(payload), encoding="utf-8")
            findings, _ = MODULE.scan_controlled_candidate_files(root, paths)
            self.assertIn("artifact:phone", {finding.rule for finding in findings})

    def test_unbound_sidecar_attestation_url_is_not_suppressed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._fixtures(root)
            final_path = root / paths[0]
            payload = json.loads(final_path.read_text(encoding="utf-8"))
            payload["whatsapp_sidecar_attestation"]["url"] = (
                "https://github.com/Maximvonshaft/nexus_helpdesk/attestations/12025550124"
            )
            final_path.write_text(json.dumps(payload), encoding="utf-8")
            findings, _ = MODULE.scan_controlled_candidate_files(root, paths)
            self.assertIn("artifact:phone", {finding.rule for finding in findings})

    def test_phone_in_unrecognized_field_is_not_suppressed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._fixtures(root)
            final_path = root / paths[0]
            payload = json.loads(final_path.read_text(encoding="utf-8"))
            payload["operator_contact"] = "+382 67 123 456"
            final_path.write_text(json.dumps(payload), encoding="utf-8")
            findings, _ = MODULE.scan_controlled_candidate_files(root, paths)
            self.assertIn("artifact:phone", {finding.rule for finding in findings})

    def test_non_date_compliance_value_is_not_suppressed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._fixtures(root)
            binding_path = root / paths[2]
            payload = json.loads(binding_path.read_text(encoding="utf-8"))
            payload["evaluated_on"] = "+382 67 123 456"
            binding_path.write_text(json.dumps(payload), encoding="utf-8")
            findings, _ = MODULE.scan_controlled_candidate_files(root, paths)
            self.assertIn("artifact:phone", {finding.rule for finding in findings})

    def test_invalid_calendar_date_is_not_suppressed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._fixtures(root)
            binding_path = root / paths[2]
            payload = json.loads(binding_path.read_text(encoding="utf-8"))
            payload["evaluated_on"] = "2026-02-31"
            binding_path.write_text(json.dumps(payload), encoding="utf-8")
            findings, _ = MODULE.scan_controlled_candidate_files(root, paths)
            self.assertIn("artifact:phone", {finding.rule for finding in findings})

    def test_canonical_run_url_must_match_numeric_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._fixtures(root)
            receipt_path = root / paths[6]
            payload = json.loads(receipt_path.read_text(encoding="utf-8"))
            payload["run_url"] = (
                "https://github.com/Maximvonshaft/nexus_helpdesk/actions/runs/12025550123"
            )
            receipt_path.write_text(json.dumps(payload), encoding="utf-8")
            findings, _ = MODULE.scan_controlled_candidate_files(root, paths)
            self.assertIn("artifact:phone", {finding.rule for finding in findings})

    def test_canonical_run_url_owner_must_match_workflow_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._fixtures(root)
            receipt_path = root / paths[6]
            payload = json.loads(receipt_path.read_text(encoding="utf-8"))
            payload["run_url"] = (
                "https://github.com/12025550123/nexus_helpdesk/actions/runs/"
                + str(self.canonical_run_id)
            )
            receipt_path.write_text(json.dumps(payload), encoding="utf-8")
            findings, _ = MODULE.scan_controlled_candidate_files(root, paths)
            self.assertIn("artifact:phone", {finding.rule for finding in findings})

    def test_canonical_run_url_repo_must_match_workflow_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._fixtures(root)
            receipt_path = root / paths[6]
            payload = json.loads(receipt_path.read_text(encoding="utf-8"))
            payload["run_url"] = (
                "https://github.com/Maximvonshaft/12025550123/actions/runs/"
                + str(self.canonical_run_id)
            )
            receipt_path.write_text(json.dumps(payload), encoding="utf-8")
            findings, _ = MODULE.scan_controlled_candidate_files(root, paths)
            self.assertIn("artifact:phone", {finding.rule for finding in findings})

    def test_canonical_run_url_fails_closed_without_workflow_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._fixtures(root)
            os.environ.pop("GITHUB_REPOSITORY", None)
            findings, _ = MODULE.scan_controlled_candidate_files(root, paths)
            self.assertIn("artifact:phone", {finding.rule for finding in findings})

    def test_canonical_receipt_must_keep_exact_governed_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._fixtures(root)
            receipt_path = root / paths[6]
            payload = json.loads(receipt_path.read_text(encoding="utf-8"))
            payload["operator_contact"] = "+382 67 123 456"
            receipt_path.write_text(json.dumps(payload), encoding="utf-8")
            findings, _ = MODULE.scan_controlled_candidate_files(root, paths)
            phone_findings = [finding for finding in findings if finding.rule == "artifact:phone"]
            self.assertGreaterEqual(len(phone_findings), 2)

    def test_canonical_receipt_requires_successful_push_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._fixtures(root)
            receipt_path = root / paths[6]
            payload = json.loads(receipt_path.read_text(encoding="utf-8"))
            payload["event"] = "pull_request"
            receipt_path.write_text(json.dumps(payload), encoding="utf-8")
            findings, _ = MODULE.scan_controlled_candidate_files(root, paths)
            self.assertIn("artifact:phone", {finding.rule for finding in findings})


if __name__ == "__main__":
    unittest.main()
