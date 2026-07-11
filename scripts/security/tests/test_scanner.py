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


class SecurityScannerTests(unittest.TestCase):
    def test_secret_scanner_reports_fingerprint_without_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture_secret = "sk-proj-" + ("A" * 36)
            (root / "app.py").write_text(f'KEY = "{fixture_secret}"\n', encoding="utf-8")

            findings = scan_secret_files(root, ["app.py"])

            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].rule, "openai_key")
            encoded = json.dumps(findings[0].as_dict())
            self.assertNotIn(fixture_secret, encoded)
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
                "schema_version": "nexus_security_dependency_assurance_v1",
                "status": "fail",
                "exit_codes": {
                    "pip_audit": 1,
                    "pip_sbom": 1,
                    "npm_audit": 1,
                    "npm_sbom": 0,
                },
                "python_vulnerability_count": 1,
                "npm_vulnerability_counts": {
                    "info": 0,
                    "low": 1,
                    "moderate": 0,
                    "high": 1,
                    "critical": 0,
                    "total": 2,
                },
                "findings": [
                    {
                        "ecosystem": "python",
                        "package": "PyJWT",
                        "version": "2.10.1",
                        "advisory": "CVE-2026-32597",
                        "fix_versions": ["2.13.0"],
                    },
                    {
                        "ecosystem": "npm",
                        "package": "vite",
                        "severity": "high",
                        "advisories": ["GHSA-xxxx-yyyy-zzzz"],
                        "fix_available": True,
                    },
                ],
                "findings_truncated": False,
                "sbom_sha256": {"python": "a" * 64, "webapp": "b" * 64},
            }
            (root / "dependency.json").write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(scan_artifact_files(root, ["dependency.json"]), [])

    def test_dependency_report_rejects_free_text_or_extra_sensitive_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = {
                "schema_version": "nexus_security_dependency_assurance_v1",
                "status": "pass",
                "exit_codes": {
                    "pip_audit": 0,
                    "pip_sbom": 0,
                    "npm_audit": 0,
                    "npm_sbom": 0,
                },
                "python_vulnerability_count": 0,
                "npm_vulnerability_counts": {"total": 0},
                "findings": [],
                "findings_truncated": False,
                "sbom_sha256": {"python": None, "webapp": None},
                "provider_payload": {"authorization": "Bearer " + ("A" * 30)},
            }
            (root / "dependency.json").write_text(json.dumps(payload), encoding="utf-8")
            findings = scan_artifact_files(root, ["dependency.json"])
            self.assertEqual([finding.rule for finding in findings], ["dependency_report_invalid"])

    def test_invalid_json_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "broken.json").write_text("{", encoding="utf-8")
            findings = scan_artifact_files(root, ["broken.json"])
            self.assertEqual([finding.rule for finding in findings], ["invalid_json"])

    def test_exact_unexpired_allowlist_suppresses_only_matching_finding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime_token = "ghp_" + ("A" * 36)
            (root / "one.py").write_text(f"token = '{runtime_token}'\n", encoding="utf-8")
            findings = scan_secret_files(root, ["one.py"])
            self.assertEqual(len(findings), 1)
            allowlist_path = root / "allowlist.json"
            allowlist_path.write_text(
                json.dumps(
                    {
                        "schema_version": "nexus_secret_scan_allowlist_v1",
                        "entries": [
                            {
                                "path": findings[0].path,
                                "rule": findings[0].rule,
                                "fingerprint": findings[0].fingerprint,
                                "reason": "Synthetic negative-test token only.",
                                "expires_on": "2026-08-11",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            entries = load_allowlist(allowlist_path, today=date(2026, 7, 11))
            remaining, suppressed = apply_allowlist(findings, entries)
            self.assertEqual(remaining, [])
            self.assertEqual(suppressed, 1)

    def test_expired_or_broad_allowlist_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "allowlist.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": "nexus_secret_scan_allowlist_v1",
                        "entries": [
                            {
                                "path": "backend/tests/test.py",
                                "rule": "openai_key",
                                "fingerprint": "a" * 16,
                                "reason": "Synthetic negative-test token only.",
                                "expires_on": "2026-07-10",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_allowlist(path, today=date(2026, 7, 11))

    def test_report_is_bounded_and_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime_token = "ghp_" + ("A" * 36)
            (root / "one.py").write_text(f"token = '{runtime_token}'\n", encoding="utf-8")
            findings = scan_secret_files(root, ["one.py"])
            report = bounded_report(schema="test_v1", findings=findings, scanned_files=1)
            output = root / "report.json"
            write_report(output, report)

            loaded = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(loaded["status"], "fail")
            self.assertEqual(loaded["finding_count"], 1)
            self.assertEqual(loaded["suppressed_count"], 0)
            self.assertLess(output.stat().st_size, 65536)


if __name__ == "__main__":
    unittest.main()
