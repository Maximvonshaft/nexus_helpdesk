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

from release_image_assurance import (  # noqa: E402
    AssuranceError,
    build_manifest,
    evaluate_licenses,
    evaluate_vulnerabilities,
)


class ReleaseImageAssuranceTests(unittest.TestCase):
    def _write(self, root: Path, name: str, payload) -> Path:
        path = root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_unexcepted_high_vulnerability_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = self._write(
                root,
                "trivy.json",
                {
                    "Results": [
                        {
                            "Target": "candidate",
                            "Vulnerabilities": [
                                {
                                    "VulnerabilityID": "CVE-2026-0001",
                                    "PkgName": "sample-lib",
                                    "InstalledVersion": "1.0.0",
                                    "FixedVersion": "1.0.1",
                                    "Severity": "HIGH",
                                    "Description": "must not enter bounded output",
                                }
                            ],
                        }
                    ]
                },
            )
            exceptions = self._write(
                root,
                "exceptions.json",
                {"schema_version": "nexus_container_vulnerability_exceptions_v1", "entries": []},
            )
            output = root / "summary.json"

            code = evaluate_vulnerabilities(report, exceptions, output, today=date(2026, 7, 11))
            payload = json.loads(output.read_text(encoding="utf-8"))

            self.assertEqual(code, 1)
            self.assertEqual(payload["status"], "fail")
            self.assertEqual(payload["unresolved_count"], 1)
            self.assertNotIn("Description", output.read_text(encoding="utf-8"))

    def test_exact_expiring_vulnerability_exception_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = self._write(
                root,
                "trivy.json",
                {
                    "Results": [
                        {
                            "Vulnerabilities": [
                                {
                                    "VulnerabilityID": "CVE-2026-0001",
                                    "PkgName": "sample-lib",
                                    "InstalledVersion": "1.0.0",
                                    "Severity": "CRITICAL",
                                }
                            ]
                        }
                    ]
                },
            )
            exceptions = self._write(
                root,
                "exceptions.json",
                {
                    "schema_version": "nexus_container_vulnerability_exceptions_v1",
                    "entries": [
                        {
                            "vulnerability_id": "CVE-2026-0001",
                            "package": "sample-lib",
                            "installed_version": "1.0.0",
                            "reason": "Temporary upstream fix is scheduled and tracked.",
                            "expires_on": "2026-08-01",
                            "owner": "security",
                        }
                    ],
                },
            )
            output = root / "summary.json"

            code = evaluate_vulnerabilities(report, exceptions, output, today=date(2026, 7, 11))

            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.read_text())["applied_exception_count"], 1)

    def test_expired_or_unused_vulnerability_exception_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            empty_report = self._write(root, "trivy.json", {"Results": []})
            expired = self._write(
                root,
                "expired.json",
                {
                    "schema_version": "nexus_container_vulnerability_exceptions_v1",
                    "entries": [
                        {
                            "vulnerability_id": "CVE-2026-0001",
                            "package": "sample-lib",
                            "installed_version": "1.0.0",
                            "reason": "Expired exception must never authorize a release.",
                            "expires_on": "2026-07-10",
                            "owner": "security",
                        }
                    ],
                },
            )
            with self.assertRaises(AssuranceError):
                evaluate_vulnerabilities(empty_report, expired, root / "expired-summary.json", today=date(2026, 7, 11))

            unused = self._write(
                root,
                "unused.json",
                {
                    "schema_version": "nexus_container_vulnerability_exceptions_v1",
                    "entries": [
                        {
                            "vulnerability_id": "CVE-2026-9999",
                            "package": "unused-lib",
                            "installed_version": "9.9.9",
                            "reason": "Specific temporary exception for a tracked advisory.",
                            "expires_on": "2026-08-01",
                            "owner": "security",
                        }
                    ],
                },
            )
            output = root / "unused-summary.json"
            self.assertEqual(evaluate_vulnerabilities(empty_report, unused, output, today=date(2026, 7, 11)), 1)
            self.assertEqual(json.loads(output.read_text())["unused_exception_count"], 1)

    def test_license_policy_blocks_unknown_and_denied_without_exact_exception(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sbom = self._write(
                root,
                "sbom.json",
                {
                    "bomFormat": "CycloneDX",
                    "components": [
                        {
                            "name": "allowed-lib",
                            "version": "1",
                            "purl": "pkg:pypi/allowed-lib@1",
                            "licenses": [{"license": {"id": "MIT"}}],
                        },
                        {
                            "name": "denied-lib",
                            "version": "2",
                            "purl": "pkg:pypi/denied-lib@2",
                            "licenses": [{"license": {"id": "AGPL-3.0-only"}}],
                        },
                        {
                            "name": "unknown-lib",
                            "version": "3",
                            "purl": "pkg:pypi/unknown-lib@3",
                            "licenses": [],
                        },
                    ],
                },
            )
            policy = self._write(
                root,
                "policy.json",
                {
                    "schema_version": "nexus_container_license_policy_v1",
                    "allowed": ["MIT"],
                    "denied": ["AGPL-3.0-only"],
                    "review": [],
                    "unknown_action": "review",
                    "exceptions": [],
                },
            )
            output = root / "licenses.json"

            code = evaluate_licenses(sbom, policy, output, today=date(2026, 7, 11))
            payload = json.loads(output.read_text())

            self.assertEqual(code, 1)
            self.assertEqual(payload["unresolved_count"], 2)
            self.assertEqual({item["disposition"] for item in payload["findings"]}, {"denied", "review"})

    def test_license_expression_and_exact_purl_exception(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            purl = "pkg:pypi/mixed-lib@1.2.3"
            sbom = self._write(
                root,
                "sbom.json",
                {
                    "bomFormat": "CycloneDX",
                    "components": [
                        {
                            "name": "mixed-lib",
                            "version": "1.2.3",
                            "purl": purl,
                            "licenses": [{"expression": "MIT OR GPL-3.0-only"}],
                        }
                    ],
                },
            )
            policy = self._write(
                root,
                "policy.json",
                {
                    "schema_version": "nexus_container_license_policy_v1",
                    "allowed": ["MIT"],
                    "denied": [],
                    "review": ["GPL-3.0-only"],
                    "unknown_action": "review",
                    "exceptions": [
                        {
                            "purl": purl,
                            "package": "mixed-lib",
                            "version": "1.2.3",
                            "license": "GPL-3.0-only",
                            "reason": "Runtime distribution review approved for this exact component.",
                            "expires_on": "2026-08-01",
                            "owner": "legal",
                        }
                    ],
                },
            )
            output = root / "licenses.json"

            self.assertEqual(evaluate_licenses(sbom, policy, output, today=date(2026, 7, 11)), 0)
            self.assertEqual(json.loads(output.read_text())["applied_exception_count"], 1)

    def test_same_name_version_license_across_ecosystems_requires_separate_exceptions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            python_purl = "pkg:pypi/shared-name@1.0.0"
            npm_purl = "pkg:npm/shared-name@1.0.0"
            sbom = self._write(
                root,
                "sbom.json",
                {
                    "bomFormat": "CycloneDX",
                    "components": [
                        {
                            "name": "shared-name",
                            "version": "1.0.0",
                            "purl": python_purl,
                            "licenses": [{"license": {"id": "LGPL-3.0-only"}}],
                        },
                        {
                            "name": "shared-name",
                            "version": "1.0.0",
                            "purl": npm_purl,
                            "licenses": [{"license": {"id": "LGPL-3.0-only"}}],
                        },
                    ],
                },
            )
            policy = self._write(
                root,
                "policy.json",
                {
                    "schema_version": "nexus_container_license_policy_v1",
                    "allowed": [],
                    "denied": [],
                    "review": ["LGPL-3.0-only"],
                    "unknown_action": "review",
                    "exceptions": [
                        {
                            "purl": python_purl,
                            "package": "shared-name",
                            "version": "1.0.0",
                            "license": "LGPL-3.0-only",
                            "reason": "Only the Python component has complete compliance evidence.",
                            "expires_on": "2026-08-01",
                            "owner": "legal",
                        }
                    ],
                },
            )
            output = root / "licenses.json"

            self.assertEqual(evaluate_licenses(sbom, policy, output, today=date(2026, 7, 11)), 1)
            payload = json.loads(output.read_text())
            self.assertEqual(payload["applied_exception_count"], 1)
            self.assertEqual(payload["unresolved_count"], 1)
            self.assertEqual(payload["findings"][0]["purl"], npm_purl)

    def test_manifest_binds_source_image_and_evidence_digests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sbom = self._write(root, "sbom.json", {"bomFormat": "CycloneDX", "components": []})
            vulnerabilities = self._write(
                root,
                "vulnerabilities.json",
                {"status": "pass", "counts": {"CRITICAL": 0, "HIGH": 0}},
            )
            licenses = self._write(root, "licenses.json", {"status": "pass", "unresolved_count": 0})
            output = root / "manifest.json"

            code = build_manifest(
                source_sha="a" * 40,
                image_id="sha256:" + ("b" * 64),
                sbom_path=sbom,
                vulnerability_summary_path=vulnerabilities,
                license_summary_path=licenses,
                output_path=output,
            )
            payload = json.loads(output.read_text())

            self.assertEqual(code, 0)
            self.assertEqual(payload["status"], "pass")
            self.assertEqual(payload["source_sha"], "a" * 40)
            self.assertFalse(payload["image_pushed"])
            self.assertFalse(payload["deployment_performed"])
            self.assertTrue(payload["sbom_sha256"].startswith("sha256:"))


if __name__ == "__main__":
    unittest.main()
