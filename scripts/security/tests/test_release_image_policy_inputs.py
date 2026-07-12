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
                            "purl": "pkg:pypi/sample@1",
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
        }

    def test_valid_complete_inputs_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = self._valid_inputs(root)
            output = root / "result.json"

            self.assertEqual(
                validate(
                    trivy_path=inputs["trivy"],
                    sbom_path=inputs["sbom"],
                    vulnerability_exceptions_path=inputs["vulnerability_exceptions"],
                    license_policy_path=inputs["license_policy"],
                    output_path=output,
                    today=date(2026, 7, 12),
                ),
                0,
            )
            result = json.loads(output.read_text())
            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["evaluated_on"], "2026-07-12")

    def test_missing_trivy_results_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = self._valid_inputs(root)
            inputs["trivy"] = self._write(root, "trivy.json", {})

            with self.assertRaisesRegex(PolicyInputError, "trivy_results_missing"):
                validate(
                    trivy_path=inputs["trivy"],
                    sbom_path=inputs["sbom"],
                    vulnerability_exceptions_path=inputs["vulnerability_exceptions"],
                    license_policy_path=inputs["license_policy"],
                    output_path=root / "result.json",
                    today=date(2026, 7, 12),
                )

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
                validate(
                    trivy_path=inputs["trivy"],
                    sbom_path=inputs["sbom"],
                    vulnerability_exceptions_path=inputs["vulnerability_exceptions"],
                    license_policy_path=inputs["license_policy"],
                    output_path=root / "result.json",
                    today=date(2026, 7, 12),
                )

    def test_unaccountable_exception_owner_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = self._valid_inputs(root)
            inputs["license_policy"] = self._write(
                root,
                "license-policy.json",
                {
                    "schema_version": "nexus_container_license_policy_v1",
                    "allowed": ["MIT"],
                    "denied": [],
                    "review": ["LGPL-3.0-only"],
                    "unknown_action": "review",
                    "exceptions": [
                        {
                            "package": "sample",
                            "version": "1",
                            "license": "LGPL-3.0-only",
                            "owner": "unassigned",
                            "expires_on": "2026-08-01",
                            "reason": "Temporary exact review for this candidate dependency.",
                        }
                    ],
                },
            )

            with self.assertRaisesRegex(
                PolicyInputError, "exception_owner_unaccountable"
            ):
                validate(
                    trivy_path=inputs["trivy"],
                    sbom_path=inputs["sbom"],
                    vulnerability_exceptions_path=inputs["vulnerability_exceptions"],
                    license_policy_path=inputs["license_policy"],
                    output_path=root / "result.json",
                    today=date(2026, 7, 12),
                )

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
                validate(
                    trivy_path=inputs["trivy"],
                    sbom_path=inputs["sbom"],
                    vulnerability_exceptions_path=inputs["vulnerability_exceptions"],
                    license_policy_path=inputs["license_policy"],
                    output_path=root / "result.json",
                    today=date(2026, 7, 12),
                )


if __name__ == "__main__":
    unittest.main()
