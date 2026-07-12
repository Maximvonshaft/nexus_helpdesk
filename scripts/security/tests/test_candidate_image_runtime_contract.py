from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
SECURITY_ROOT = Path(__file__).resolve().parents[1]
if str(SECURITY_ROOT) not in sys.path:
    sys.path.insert(0, str(SECURITY_ROOT))

from validate_release_image_evidence import validate_evidence_set  # noqa: E402


class CandidateImageRuntimeContractTests(unittest.TestCase):
    SOURCE_SHA = "a" * 40
    IMAGE_ID = "sha256:" + "b" * 64
    DIGEST = "sha256:" + "c" * 64

    def _write(self, root: Path, name: str, payload: object) -> Path:
        path = root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _complete_bundle(self, root: Path) -> dict[str, Path]:
        root.mkdir(parents=True, exist_ok=True)
        root.joinpath("raw-cleanup-exit-code").write_text("0\n", encoding="utf-8")
        root.joinpath("artifact-scan-exit-code").write_text("0\n", encoding="utf-8")
        sbom = self._write(
            root,
            "image.safe.cdx.json",
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.6",
                "version": 1,
                "metadata": {
                    "properties": [
                        {
                            "name": "nexus:source-sbom-sha256",
                            "value": self.DIGEST,
                        }
                    ]
                },
                "components": [
                    {
                        "bom-ref": "pkg:pypi/psycopg@3.2.6",
                        "type": "library",
                        "name": "psycopg",
                        "version": "3.2.6",
                        "purl": "pkg:pypi/psycopg@3.2.6",
                        "licenses": [{"license": {"id": "LGPL-3.0-only"}}],
                    }
                ],
            },
        )
        sbom_summary = self._write(
            root,
            "image.safe.cdx.json.summary.json",
            {
                "schema_version": "nexus_finalized_image_sbom_v1",
                "status": "pass",
            },
        )
        raw_digests = self._write(
            root,
            "raw-evidence-digests.json",
            {
                "schema_version": "nexus_raw_release_evidence_digests_v2",
                "trivy_report_sha256": self.DIGEST,
                "raw_cyclonedx_sha256": self.DIGEST,
                "raw_frontend_cyclonedx_sha256": self.DIGEST,
            },
        )
        vulnerabilities = self._write(
            root,
            "vulnerability-summary.json",
            {
                "schema_version": "nexus_container_vulnerability_assurance_v1",
                "status": "pass",
            },
        )
        licenses = self._write(
            root,
            "license-summary.json",
            {
                "schema_version": "nexus_container_license_assurance_v1",
                "status": "pass",
            },
        )
        manifest = self._write(
            root,
            "release-image-manifest.json",
            {
                "schema_version": "nexus_release_image_assurance_v1",
                "status": "pass",
                "source_sha": self.SOURCE_SHA,
                "image_id": self.IMAGE_ID,
                "sbom_sha256": self.DIGEST,
                "vulnerability_summary_sha256": self.DIGEST,
                "license_summary_sha256": self.DIGEST,
                "vulnerability_status": "pass",
                "license_status": "pass",
                "critical_count": 0,
                "high_count": 0,
                "unresolved_license_count": 0,
                "image_pushed": False,
                "deployment_performed": False,
            },
        )
        self._write(
            root,
            "policy-input-validation.json",
            {
                "schema_version": "nexus_release_image_policy_input_validation_v1",
                "status": "pass",
            },
        )
        self._write(
            root,
            "installed-license-evidence.json",
            {
                "schema_version": "nexus_installed_license_evidence_v1",
                "components": [
                    {
                        "purl": "pkg:pypi/psycopg@3.2.6",
                        "package": "psycopg",
                        "version": "3.2.6",
                        "license_files": [
                            {
                                "path": "psycopg-3.2.6.dist-info/licenses/LICENSE.txt",
                                "sha256": self.DIGEST,
                            }
                        ],
                    }
                ],
            },
        )
        self._write(
            root,
            "license-compliance-evidence.json",
            {
                "schema_version": "nexus_container_license_compliance_evidence_v1",
                "status": "pass",
            },
        )
        self._write(
            root,
            "release-image-compliance-binding.json",
            {
                "schema_version": "nexus_release_image_compliance_binding_v1",
                "status": "pass",
                "source_sha": self.SOURCE_SHA,
                "image_id": self.IMAGE_ID,
                "base_manifest_sha256": self.DIGEST,
                "policy_input_validation_sha256": self.DIGEST,
                "license_compliance_sha256": self.DIGEST,
                "installed_license_evidence_sha256": self.DIGEST,
                "image_pushed": False,
                "deployment_performed": False,
            },
        )
        return {
            "sbom": sbom,
            "sbom_summary": sbom_summary,
            "raw_digests": raw_digests,
            "vulnerabilities": vulnerabilities,
            "licenses": licenses,
            "manifest": manifest,
        }

    def _validate(self, root: Path, paths: dict[str, Path]) -> int:
        return validate_evidence_set(
            sbom=paths["sbom"],
            sbom_summary=paths["sbom_summary"],
            raw_digests=paths["raw_digests"],
            vulnerabilities=paths["vulnerabilities"],
            licenses=paths["licenses"],
            manifest=paths["manifest"],
            output=root / "structured-evidence-scan.json",
        )

    def test_candidate_app_healthcheck_uses_python_not_curl(self) -> None:
        compose = (ROOT / "deploy" / "docker-compose.candidate.yml").read_text(
            encoding="utf-8"
        )
        app_section = compose.split("  app-candidate:", 1)[1].split(
            "  worker-outbound-candidate:", 1
        )[0]
        self.assertNotIn('"curl"', app_section)
        self.assertIn("urllib.request.urlopen", app_section)
        self.assertIn("http://127.0.0.1:8080/readyz", app_section)
        self.assertIn("assert response.status == 200", app_section)

    def test_cleanup_scan_quarantine_and_upload_have_one_safe_order(self) -> None:
        workflow = (
            ROOT / ".github" / "workflows" / "release-image-assurance.yml"
        ).read_text(encoding="utf-8")
        cleanup_marker = "      - name: Remove raw third-party metadata before evidence upload\n"
        scan_marker = "      - name: Scan bounded free-text evidence\n"
        structured_marker = "      - name: Validate structured evidence schemas\n"
        upload_marker = "      - name: Upload bounded release-image evidence\n"
        enforce_marker = "      - name: Enforce release image gate\n"
        cleanup_start = workflow.index(cleanup_marker)
        scan_start = workflow.index(scan_marker)
        structured_start = workflow.index(structured_marker)
        upload_start = workflow.index(upload_marker)
        enforce_start = workflow.index(enforce_marker)
        cleanup_block = workflow[cleanup_start:scan_start]
        scan_block = workflow[scan_start:structured_start]
        structured_block = workflow[structured_start:upload_start]
        upload_block = workflow[upload_start:enforce_start]
        self.assertIn("        if: always()\n", cleanup_block)
        self.assertIn("raw-cleanup-exit-code", cleanup_block)
        self.assertIn("cleanup_code=1", cleanup_block)
        self.assertIn("        if: always()\n", scan_block)
        self.assertIn("raw-cleanup-exit-code", scan_block)
        self.assertIn("        if: always()\n", structured_block)
        self.assertIn("raw_cleanup_not_clean", structured_block)
        self.assertIn("unsafe_artifacts_uploaded", structured_block)
        self.assertIn("upload_safe=true", structured_block)
        self.assertIn('echo "upload_safe=${upload_safe}"', structured_block)
        self.assertIn(
            "if: ${{ always() && steps.structured_scan.outputs.upload_safe == 'true' }}",
            upload_block,
        )
        self.assertLess(cleanup_start, scan_start)
        self.assertLess(scan_start, structured_start)
        self.assertLess(structured_start, upload_start)
        self.assertLess(upload_start, enforce_start)

    def test_complete_validated_bundle_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "artifacts" / "release-image"
            paths = self._complete_bundle(root)
            self.assertEqual(self._validate(root, paths), 0)
            report = json.loads(
                (root / "structured-evidence-scan.json").read_text()
            )
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["validated_files"], 10)

    def test_failed_binding_quarantines_complete_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "artifacts" / "release-image"
            paths = self._complete_bundle(root)
            binding = root / "release-image-compliance-binding.json"
            payload = json.loads(binding.read_text())
            payload["status"] = "fail"
            binding.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(self._validate(root, paths), 1)
            self.assertEqual(
                {path.name for path in root.iterdir()},
                {
                    "release-image-quarantine.json",
                    "structured-evidence-scan.json",
                },
            )
            quarantine = json.loads(
                (root / "release-image-quarantine.json").read_text()
            )
            self.assertEqual(quarantine["status"], "quarantined")
            self.assertFalse(quarantine["unsafe_artifacts_uploaded"])
            self.assertEqual(quarantine["reason"], "binding_not_pass")

    def test_failed_artifact_scan_quarantines_all_candidate_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact_dir = Path(directory) / "artifacts" / "release-image"
            artifact_dir.mkdir(parents=True)
            artifact_dir.joinpath("raw-cleanup-exit-code").write_text(
                "0\n", encoding="utf-8"
            )
            artifact_dir.joinpath("artifact-scan-exit-code").write_text(
                "1\n", encoding="utf-8"
            )
            unsafe_summary = "Bear" + "er unsafe-value-that-must-not-be-uploaded\n"
            artifact_dir.joinpath("candidate-build-summary.txt").write_text(
                unsafe_summary, encoding="utf-8"
            )
            artifact_dir.joinpath("image.safe.cdx.json").write_text(
                '{"unsafe":"payload"}\n', encoding="utf-8"
            )
            output = artifact_dir / "structured-evidence-scan.json"
            result = validate_evidence_set(
                sbom=artifact_dir / "image.safe.cdx.json",
                sbom_summary=artifact_dir / "image.safe.cdx.json.summary.json",
                raw_digests=artifact_dir / "raw-evidence-digests.json",
                vulnerabilities=artifact_dir / "vulnerability-summary.json",
                licenses=artifact_dir / "license-summary.json",
                manifest=artifact_dir / "release-image-manifest.json",
                output=output,
            )
            self.assertEqual(result, 1)
            self.assertEqual(
                {path.name for path in artifact_dir.iterdir()},
                {
                    "release-image-quarantine.json",
                    "structured-evidence-scan.json",
                },
            )
            quarantine = json.loads(
                (artifact_dir / "release-image-quarantine.json").read_text()
            )
            self.assertEqual(quarantine["status"], "quarantined")
            self.assertFalse(quarantine["unsafe_artifacts_uploaded"])
            self.assertEqual(quarantine["reason"], "artifact_scan_not_clean")


if __name__ == "__main__":
    unittest.main()
