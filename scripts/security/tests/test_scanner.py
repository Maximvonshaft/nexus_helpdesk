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

from scanner import (
    apply_allowlist,
    bounded_report,
    load_allowlist,
    scan_artifact_files,
    scan_secret_files,
    write_report,
)


def _synthetic_openai_token() -> str:
    # Build the test-only matcher input from code points so the repository does
    # not itself contain or store a clear-text credential-shaped literal.
    prefix = bytes((115, 107, 45, 112, 114, 111, 106, 45)).decode("ascii")
    return prefix + ("A" * 36)


class SecurityScannerTests(unittest.TestCase):
    def test_secret_scanner_reports_fingerprint_without_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            synthetic_token = _synthetic_openai_token()
            (root / "app.py").write_text(
                "KEY = " + json.dumps(synthetic_token) + "\n",
                encoding="utf-8",
            )

            findings = scan_secret_files(root, ["app.py"])

            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].rule, "openai_key")
            encoded = json.dumps(findings[0].as_dict())
            self.assertNotIn(synthetic_token, encoded)
            self.assertEqual(len(findings[0].fingerprint), 16)

    def test_placeholders_are_not_false_positive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "example.md").write_text(
                "example Authorization: Bearer ABCDEFGHIJKLMNOPQRSTUVWXYZ012345\n"
                "${{ secrets.OPENAI_API_KEY }}\n",
                encoding="utf-8",
            )
            self.assertEqual(scan_secret_files(root, ["example.md"]), [])

    def test_artifact_scanner_rejects_sensitive_keys_and_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = {
                "status": "failed",
                "provider_payload": {"email": "person@example.com"},
                "fixture_value": "AKIA" + ("A" * 16),
            }
            (root / "artifact.json").write_text(json.dumps(payload), encoding="utf-8")

            findings = scan_artifact_files(root, ["artifact.json"])
            rules = {finding.rule for finding in findings}

            self.assertIn("json_key:provider_payload", rules)
            self.assertIn("json_key:email", rules)
            self.assertIn("artifact:email", rules)
            self.assertIn("artifact:aws_access_key", rules)
            self.assertNotIn("person@example.com", json.dumps([finding.as_dict() for finding in findings]))

    def test_hash_and_fingerprint_values_are_not_treated_as_tracking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = {
                "fingerprint": "08842a2e1e69f699",
                "source_sha256": "a" * 64,
                "nested": {"payload_digest": "39a06696c62f7181"},
            }
            (root / "artifact.json").write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(scan_artifact_files(root, ["artifact.json"]), [])

    def test_dependency_report_allows_only_bounded_security_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = {
                "schema_version": "nexus_dependency_assurance_v1",
                "python": {
                    "exit_code": 1,
                    "vulnerability_count": 1,
                    "vulnerabilities": [
                        {
                            "package": "pypdf",
                            "version": "6.10.2",
                            "id": "CVE-2026-0001",
                            "fix_versions": ["6.13.3"],
                        }
                    ],
                },
                "node": {
                    "exit_code": 0,
                    "metadata": {"vulnerabilities": {"info": 0, "low": 0, "moderate": 0, "high": 0, "critical": 0, "total": 0}},
                    "advisories": [],
                },
                "sbom": {
                    "python_sha256": "a" * 64,
                    "webapp_sha256": "b" * 64,
                },
            }
            (root / "dependency-summary.json").write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(scan_artifact_files(root, ["dependency-summary.json"]), [])

    def test_dependency_report_rejects_extra_free_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = {
                "schema_version": "nexus_dependency_assurance_v1",
                "python": {"exit_code": 0, "vulnerability_count": 0, "vulnerabilities": []},
                "node": {"exit_code": 0, "metadata": {"vulnerabilities": {"info": 0, "low": 0, "moderate": 0, "high": 0, "critical": 0, "total": 0}}, "advisories": []},
                "sbom": {"python_sha256": "a" * 64, "webapp_sha256": "b" * 64},
                "unexpected": "person@example.com",
            }
            (root / "dependency-summary.json").write_text(json.dumps(payload), encoding="utf-8")
            findings = scan_artifact_files(root, ["dependency-summary.json"])
            self.assertTrue(findings)

    def test_allowlist_requires_exact_fingerprint_and_future_expiry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            synthetic_token = _synthetic_openai_token()
            (root / "fixture.py").write_text(json.dumps(synthetic_token), encoding="utf-8")
            findings = scan_secret_files(root, ["fixture.py"])
            self.assertEqual(len(findings), 1)
            finding = findings[0]
            allowlist_path = root / "allowlist.json"
            allowlist_path.write_text(
                json.dumps(
                    {
                        "schema_version": "nexus_secret_scan_allowlist_v1",
                        "entries": [
                            {
                                "path": finding.path,
                                "rule": finding.rule,
                                "fingerprint": finding.fingerprint,
                                "reason": "Synthetic test fixture",
                                "expires_on": "2099-01-01",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            allowlist = load_allowlist(allowlist_path)
            remaining, applied = apply_allowlist(findings, allowlist, today=date(2026, 7, 11))
            self.assertEqual(remaining, [])
            self.assertEqual(len(applied), 1)

    def test_bounded_report_never_contains_matches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            synthetic_token = _synthetic_openai_token()
            (root / "fixture.py").write_text(json.dumps(synthetic_token), encoding="utf-8")
            findings = scan_secret_files(root, ["fixture.py"])
            report = bounded_report(
                scanner="unit-test",
                findings=findings,
                scanned_files=1,
                max_report_bytes=2048,
            )
            output = root / "report.json"
            write_report(output, report, max_bytes=2048)
            encoded = output.read_text(encoding="utf-8")
            self.assertNotIn(synthetic_token, encoded)
            self.assertLessEqual(len(encoded.encode("utf-8")), 2048)


if __name__ == "__main__":
    unittest.main()
