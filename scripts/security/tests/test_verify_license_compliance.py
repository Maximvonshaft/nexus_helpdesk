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
                    }
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


if __name__ == "__main__":
    unittest.main()
