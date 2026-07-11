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
                        "purl": "pkg:pypi/psycopg@3.2.6",
                        "license": "LGPL-3.0-only",
                        "owner": "open-source-compliance",
                        "expires_on": "2026-09-30",
                        "source": "https://github.com/psycopg/psycopg/tree/3.2.6",
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
                        "license": "LGPL-3.0-only",
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
                        "purl": "pkg:pypi/psycopg@3.2.6",
                        "licenses": [{"license": {"id": "LGPL-3.0-only"}}],
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
        notice.write_text("pkg:pypi/psycopg@3.2.6 — LGPL-3.0-only", encoding="utf-8")
        return compliance, policy, sbom, installed, notice

    def test_exact_evidence_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            compliance, policy, sbom, installed, notice = self._fixture(root)
            output = root / "out.json"
            self.assertEqual(
                verify(
                    compliance_path=compliance,
                    policy_path=policy,
                    sbom_path=sbom,
                    installed_path=installed,
                    notice_path=notice,
                    output_path=output,
                    today=date(2026, 7, 11),
                ),
                0,
            )
            self.assertEqual(json.loads(output.read_text())["status"], "pass")

    def test_missing_license_file_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            compliance, policy, sbom, installed, notice = self._fixture(root)
            data = json.loads(installed.read_text())
            data["components"][0]["license_files"] = []
            installed.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ComplianceError, "license_file_missing"):
                verify(
                    compliance_path=compliance,
                    policy_path=policy,
                    sbom_path=sbom,
                    installed_path=installed,
                    notice_path=notice,
                    output_path=root / "out.json",
                    today=date(2026, 7, 11),
                )

    def test_version_or_notice_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            compliance, policy, sbom, installed, notice = self._fixture(root)
            notice.write_text("missing exact component", encoding="utf-8")
            with self.assertRaisesRegex(ComplianceError, "notice_missing"):
                verify(
                    compliance_path=compliance,
                    policy_path=policy,
                    sbom_path=sbom,
                    installed_path=installed,
                    notice_path=notice,
                    output_path=root / "out.json",
                    today=date(2026, 7, 11),
                )


if __name__ == "__main__":
    unittest.main()
