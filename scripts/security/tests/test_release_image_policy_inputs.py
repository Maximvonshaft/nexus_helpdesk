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

from validate_release_image_policy_inputs import PolicyInputError, validate  # noqa: E402


class ReleaseImagePolicyInputTests(unittest.TestCase):
    PURL = "pkg:pypi/sample@1"

    def _write(self, root: Path, name: str, payload) -> Path:
        path = root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _valid_inputs(self, root: Path) -> dict[str, Path]:
        return {
            "trivy": self._write(root, "trivy.json", {"Results": []}),
            "sbom": self._write(
                root,
                "sbom.json",
                {
                    "bomFormat": "CycloneDX",
                    "components": [
                        {
                            "name": "sample",
                            "version": "1",
                            "purl": self.PURL,
                        }
                    ],
                },
            ),
            "vulnerability_exceptions": self._write(
                root,
                "vulnerability-exceptions.json",
                {
                    "schema_version": "nexus_container_vulnerability_exceptions_v1",
                    "entries": [],
                },
            ),
            "license_policy": self._write(
                root,
                "license-policy.json",
                {
                    "schema_version": "nexus_container_license_policy_v1",
                    "allowed": ["MIT"],
                    "denied": ["AGPL-3.0-only"],
                    "review": ["LGPL-3.0-only"],
                    "unknown_action": "review",
                    "exceptions": [],
                },
            ),
            "license_compliance": self._write(
                root,
                "license-compliance.json",
                {
                    "schema_version": "nexus_container_license_compliance_v1",
                    "entries": [],
                },
            ),
        }

    def _validate(self, inputs: dict[str, Path], output: Path) -> int:
        return validate(
            trivy_path=inputs["trivy"],
            sbom_path=inputs["sbom"],
            vulnerability_exceptions_path=inputs["vulnerability_exceptions"],
            license_policy_path=inputs["license_policy"],
            license_compliance_path=inputs["license_compliance"],
            output_path=output,
            today=date(2026, 7, 12),
        )

    def _review_exception(self, *, purl: str | None = None, owner: str = "open-source-owner"):
        return {
            "purl": purl or self.PURL,
            "package": "sample",
            "version": "1",
            "license": "LGPL-3.0-only",
            "owner": owner,
            "expires_on": "2026-08-01",
            "reason": "Exact review requires complete compliance and retained notice evidence.",
        }

    def _compliance_record(self, *, purl: str | None = None, owner: str = "open-source-owner"):
        return {
            "package": "sample",
            "version": "1",
            "purl": purl or self.PURL,
            "license": "LGPL-3.0-only",
            "owner": owner,
            "expires_on": "2026-08-01",
            "source": "https://example.invalid/sample/1",
            "notice_path": "THIRD_PARTY_NOTICES.md",
            "modified": False,
            "replacement_supported": True,
            "obligations": ["retain_license_text"],
        }

    def test_valid_complete_inputs_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = self._valid_inputs(root)
            output = root / "result.json"

            self.assertEqual(self._validate(inputs, output), 0)
            result = json.loads(output.read_text())
            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["evaluated_on"], "2026-07-12")
            self.assertEqual(result["license_compliance_record_count"], 0)

    def test_missing_trivy_results_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = self._valid_inputs(root)
            inputs["trivy"] = self._write(root, "trivy.json", {})

            with self.assertRaisesRegex(PolicyInputError, "trivy_results_missing"):
                self._validate(inputs, root / "result.json")

    def test_empty_sbom_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = self._valid_inputs(root)
            inputs["sbom"] = self._write(
                root,
                "sbom.json",
                {"bomFormat": "CycloneDX", "components": []},
            )

            with self.assertRaisesRegex(PolicyInputError, "sbom_components_empty"):
                self._validate(inputs, root / "result.json")

    def test_duplicate_sbom_purl_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = self._valid_inputs(root)
            payload = json.loads(inputs["sbom"].read_text())
            payload["components"].append(
                {"name": "other", "version": "1", "purl": self.PURL}
            )
            inputs["sbom"].write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(PolicyInputError, "sbom_component_purl_duplicate"):
                self._validate(inputs, root / "result.json")

    def test_unaccountable_exception_owner_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = self._valid_inputs(root)
            policy = json.loads(inputs["license_policy"].read_text())
            policy["exceptions"] = [self._review_exception(owner="unassigned")]
            inputs["license_policy"].write_text(json.dumps(policy), encoding="utf-8")

            with self.assertRaisesRegex(
                PolicyInputError, "exception_owner_unaccountable"
            ):
                self._validate(inputs, root / "result.json")

    def test_denied_license_cannot_be_excepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = self._valid_inputs(root)
            policy = json.loads(inputs["license_policy"].read_text())
            forbidden = self._review_exception(purl="pkg:pypi/forbidden@1")
            forbidden.update(
                {
                    "package": "forbidden",
                    "license": "AGPL-3.0-only",
                    "reason": "This forbidden component must never become releasable.",
                }
            )
            policy["exceptions"] = [forbidden]
            inputs["license_policy"].write_text(json.dumps(policy), encoding="utf-8")

            with self.assertRaisesRegex(
                PolicyInputError, "license_exception_for_denied_license"
            ):
                self._validate(inputs, root / "result.json")

    def test_review_exception_requires_matching_compliance_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = self._valid_inputs(root)
            policy = json.loads(inputs["license_policy"].read_text())
            policy["exceptions"] = [self._review_exception()]
            inputs["license_policy"].write_text(json.dumps(policy), encoding="utf-8")

            with self.assertRaisesRegex(
                PolicyInputError, "license_exception_compliance_missing"
            ):
                self._validate(inputs, root / "result.json")

    def test_matching_review_exception_and_compliance_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = self._valid_inputs(root)
            policy = json.loads(inputs["license_policy"].read_text())
            policy["exceptions"] = [self._review_exception()]
            inputs["license_policy"].write_text(json.dumps(policy), encoding="utf-8")
            compliance = json.loads(inputs["license_compliance"].read_text())
            compliance["entries"] = [self._compliance_record()]
            inputs["license_compliance"].write_text(
                json.dumps(compliance), encoding="utf-8"
            )

            output = root / "result.json"
            self.assertEqual(self._validate(inputs, output), 0)
            self.assertEqual(
                json.loads(output.read_text())["license_compliance_record_count"], 1
            )

    def test_policy_and_compliance_same_tuple_different_purl_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = self._valid_inputs(root)
            policy = json.loads(inputs["license_policy"].read_text())
            policy["exceptions"] = [self._review_exception()]
            inputs["license_policy"].write_text(json.dumps(policy), encoding="utf-8")
            compliance = json.loads(inputs["license_compliance"].read_text())
            compliance["entries"] = [
                self._compliance_record(purl="pkg:npm/sample@1")
            ]
            inputs["license_compliance"].write_text(
                json.dumps(compliance), encoding="utf-8"
            )

            with self.assertRaisesRegex(
                PolicyInputError, "license_exception_compliance_missing"
            ):
                self._validate(inputs, root / "result.json")

    def test_expired_exception_fails_against_runtime_date(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = self._valid_inputs(root)
            inputs["vulnerability_exceptions"] = self._write(
                root,
                "vulnerability-exceptions.json",
                {
                    "schema_version": "nexus_container_vulnerability_exceptions_v1",
                    "entries": [
                        {
                            "vulnerability_id": "CVE-2026-0001",
                            "package": "sample",
                            "installed_version": "1",
                            "owner": "security-owner",
                            "expires_on": "2026-07-11",
                            "reason": "Temporary exact exception pending the upstream fixed package.",
                        }
                    ],
                },
            )

            with self.assertRaisesRegex(PolicyInputError, "exception_expired"):
                self._validate(inputs, root / "result.json")


if __name__ == "__main__":
    unittest.main()
