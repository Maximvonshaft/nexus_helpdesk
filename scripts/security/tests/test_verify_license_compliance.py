from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from verify_license_compliance import ComplianceError, verify  # noqa: E402


class VerifyLicenseComplianceTests(unittest.TestCase):
    SOURCE = "https://github.com/psycopg/psycopg/tree/3.2.6"
    PURL = "pkg:pypi/psycopg@3.2.6"
    LICENSE = "LGPL-3.0-only"

    def _write(self, root: Path, name: str, payload) -> Path:
        path = root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _fixture(self, root: Path):
        compliance = self._write(
            root,
            "compliance.json",
            {
                "schema_version": "nexus_container_license_compliance_v1",
                "entries": [
                    {
                        "package": "psycopg",
                        "version": "3.2.6",
                        "purl": self.PURL,
                        "license": self.LICENSE,
                        "owner": "open-source-compliance",
                        "expires_on": "2026-09-30",
                        "source": self.SOURCE,
                        "notice_path": "THIRD_PARTY_NOTICES.md",
                        "modified": False,
                        "replacement_supported": True,
                        "obligations": [
                            "retain_license_text",
                            "retain_copyright_notice",
                            "provide_upstream_source_reference",
                            "allow_component_replacement",
                        ],
                    }
                ],
            },
        )
        policy = self._write(
            root,
            "policy.json",
            {
                "schema_version": "nexus_container_license_policy_v1",
                "exceptions": [
                    {
                        "purl": self.PURL,
                        "package": "psycopg",
                        "version": "3.2.6",
                        "license": self.LICENSE,
                        "owner": "open-source-compliance",
                        "expires_on": "2026-09-30",
                        "reason": "Exact machine verified compliance record is required for this component.",
                    }
                ],
            },
        )
        sbom = self._write(
            root,
            "sbom.json",
            {
                "bomFormat": "CycloneDX",
                "components": [
                    {
                        "name": "psycopg",
                        "version": "3.2.6",
                        "purl": self.PURL,
                        "licenses": [{"license": {"id": self.LICENSE}}],
                    },
                    {
                        "name": "@radix-ui/primitive",
                        "version": "1.1.3",
                        "purl": "pkg:npm/%40radix-ui/primitive@1.1.3",
                        "licenses": [{"license": {"id": "MIT"}}],
                    },
                ],
            },
        )
        installed = self._write(
            root,
            "installed.json",
            {
                "schema_version": "nexus_installed_license_evidence_v1",
                "components": [
                    {
                        "purl": self.PURL,
                        "package": "psycopg",
                        "version": "3.2.6",
                        "license_files": [
                            {
                                "path": "psycopg-3.2.6.dist-info/licenses/LICENSE.txt",
                                "sha256": "sha256:" + "a" * 64,
                            }
                        ],
                    }
                ],
            },
        )
        notice = root / "THIRD_PARTY_NOTICES.md"
        notice.write_text(
            f"{self.PURL} — {self.LICENSE}\nUpstream source: {self.SOURCE}\n",
            encoding="utf-8",
        )
        return compliance, policy, sbom, installed, notice

    def _verify(self, fixture, output: Path) -> int:
        compliance, policy, sbom, installed, notice = fixture
        return verify(
            compliance_path=compliance,
            policy_path=policy,
            sbom_path=sbom,
            installed_path=installed,
            notice_path=notice,
            output_path=output,
            today=date(2026, 7, 11),
        )

    def test_exact_evidence_passes_and_records_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "out.json"
            self.assertEqual(self._verify(self._fixture(root), output), 0)
            payload = json.loads(output.read_text())
            self.assertEqual(payload["status"], "pass")
            self.assertEqual(payload["components"][0]["source"], self.SOURCE)
            self.assertEqual(payload["components"][0]["purl"], self.PURL)

    def test_missing_license_file_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = self._fixture(root)
            installed = fixture[3]
            data = json.loads(installed.read_text())
            data["components"][0]["license_files"] = []
            installed.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ComplianceError, "license_file_missing"):
                self._verify(fixture, root / "out.json")

    def test_missing_component_or_license_notice_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = self._fixture(root)
            fixture[4].write_text(
                f"Upstream source: {self.SOURCE}\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(ComplianceError, "notice_missing"):
                self._verify(fixture, root / "out.json")

    def test_missing_upstream_source_reference_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = self._fixture(root)
            fixture[4].write_text(
                f"{self.PURL} — {self.LICENSE}\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(
                ComplianceError, "notice_source_missing"
            ):
                self._verify(fixture, root / "out.json")

    def test_malformed_npm_purl_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = self._fixture(root)
            sbom = fixture[2]
            data = json.loads(sbom.read_text())
            data["components"][1]["purl"] = "pkg:npm/%40radix-ui/primitive"
            sbom.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ComplianceError, "sbom_purl_invalid"):
                self._verify(fixture, root / "out.json")

    def test_policy_exception_for_other_ecosystem_does_not_authorize_component(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = self._fixture(root)
            policy = fixture[1]
            data = json.loads(policy.read_text())
            data["exceptions"][0]["purl"] = "pkg:npm/psycopg@3.2.6"
            policy.write_text(json.dumps(data), encoding="utf-8")

            with self.assertRaisesRegex(
                ComplianceError, "policy_exception_missing"
            ):
                self._verify(fixture, root / "out.json")

    def test_installed_python_evidence_cannot_authorize_npm_component(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = self._fixture(root)
            npm_purl = "pkg:npm/psycopg@3.2.6"
            compliance = fixture[0]
            policy = fixture[1]
            sbom = fixture[2]
            notice = fixture[4]

            compliance_data = json.loads(compliance.read_text())
            compliance_data["entries"][0]["purl"] = npm_purl
            compliance.write_text(json.dumps(compliance_data), encoding="utf-8")
            policy_data = json.loads(policy.read_text())
            policy_data["exceptions"][0]["purl"] = npm_purl
            policy.write_text(json.dumps(policy_data), encoding="utf-8")
            sbom_data = json.loads(sbom.read_text())
            sbom_data["components"][0]["purl"] = npm_purl
            sbom.write_text(json.dumps(sbom_data), encoding="utf-8")
            notice.write_text(
                f"{npm_purl} — {self.LICENSE}\nUpstream source: {self.SOURCE}\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ComplianceError, "installed_component_missing"
            ):
                self._verify(fixture, root / "out.json")

    def test_extra_policy_exception_without_compliance_record_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = self._fixture(root)
            policy = fixture[1]
            data = json.loads(policy.read_text())
            data["exceptions"].append(
                {
                    "purl": "pkg:npm/psycopg@3.2.6",
                    "package": "psycopg",
                    "version": "3.2.6",
                    "license": self.LICENSE,
                    "owner": "open-source-compliance",
                    "expires_on": "2026-09-30",
                    "reason": "A second ecosystem component requires its own compliance record.",
                }
            )
            policy.write_text(json.dumps(data), encoding="utf-8")

            with self.assertRaisesRegex(
                ComplianceError, "exception_set_mismatch"
            ):
                self._verify(fixture, root / "out.json")


if __name__ == "__main__":
    unittest.main()
