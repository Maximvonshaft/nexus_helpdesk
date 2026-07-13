from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "capture_controlled_image_assurance_failure.py"
SPEC = importlib.util.spec_from_file_location("capture_controlled_image_assurance_failure", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ControlledImageAssuranceFailureTests(unittest.TestCase):
    source = "a" * 40

    def _write(self, root: Path, name: str, payload: dict | None = None) -> None:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if payload is None:
            path.write_text("{}\n", encoding="utf-8")
        else:
            path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    def _base_through_policy(self, root: Path) -> None:
        self._write(root, "image.preliminary.cdx.json")
        self._write(root, "image.safe.cdx.json")
        self._write(root, "image.safe.cdx.json.summary.json", {"status": "pass"})
        self._write(root, "policy-input-validation.json", {"status": "pass"})
        self._write(root, "installed-license-evidence.json")

    def test_vulnerability_failure_wins_before_missing_license_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._base_through_policy(root)
            self._write(
                root,
                "vulnerability-summary.json",
                {
                    "status": "fail",
                    "counts": {"CRITICAL": 0, "HIGH": 2},
                    "unresolved_count": 2,
                    "findings": [{"package": "must-not-be-copied"}],
                },
            )
            payload = MODULE.build_summary(
                release_image_dir=root,
                source_sha=self.source,
                exit_code=1,
            )
            self.assertEqual(payload["stage"], "vulnerability-policy")
            self.assertEqual(payload["signals"]["vulnerabilities"]["counts"], {"critical": 0, "high": 2})
            self.assertEqual(payload["signals"]["vulnerabilities"]["unresolved_count"], 2)
            self.assertNotIn("findings", json.dumps(payload))
            self.assertNotIn("must-not-be-copied", json.dumps(payload))

    def test_license_failure_is_bounded_to_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._base_through_policy(root)
            self._write(root, "vulnerability-summary.json", {"status": "pass", "counts": {"CRITICAL": 0, "HIGH": 0}})
            self._write(
                root,
                "license-summary.json",
                {
                    "status": "fail",
                    "counts": {"components": 50, "allowed": 49, "review": 1, "denied": 0, "unknown": 0},
                    "unresolved_count": 1,
                    "findings": [{"purl": "pkg:pypi/secret-package@1"}],
                },
            )
            payload = MODULE.build_summary(
                release_image_dir=root,
                source_sha=self.source,
                exit_code=1,
            )
            self.assertEqual(payload["stage"], "license-policy")
            self.assertEqual(payload["signals"]["licenses"]["unresolved_count"], 1)
            self.assertNotIn("purl", json.dumps(payload))
            self.assertNotIn("secret-package", json.dumps(payload))

    def test_missing_binding_is_classified_after_all_prior_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._base_through_policy(root)
            self._write(root, "vulnerability-summary.json", {"status": "pass"})
            self._write(root, "license-summary.json", {"status": "pass"})
            self._write(root, "release-image-manifest.json", {"status": "pass"})
            self._write(root, "license-compliance-evidence.json", {"status": "pass"})
            payload = MODULE.build_summary(
                release_image_dir=root,
                source_sha=self.source,
                exit_code=2,
            )
            self.assertEqual(payload["stage"], "compliance-binding")

    def test_write_and_validate_uses_root_only_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._base_through_policy(root)
            self._write(root, "vulnerability-summary.json", {"status": "fail"})
            payload = MODULE.build_summary(
                release_image_dir=root,
                source_sha=self.source,
                exit_code=1,
            )
            output = root / "failure" / "summary.json"
            MODULE.write_summary(output, payload)
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)
            self.assertEqual(MODULE.load_and_validate(output), payload)

    def test_validation_rejects_raw_or_unknown_signal_fields(self) -> None:
        payload = {
            "schema": MODULE.SCHEMA,
            "status": "failed",
            "stage": "vulnerability-policy",
            "exit_code": 1,
            "source_sha": self.source,
            "image_pushed": False,
            "deployment_performed": False,
            "signals": {name: {"present": False} for name in MODULE._SIGNAL_NAMES},
        }
        payload["signals"]["vulnerabilities"] = {
            "present": True,
            "status": "fail",
            "raw_findings": ["forbidden"],
        }
        with self.assertRaisesRegex(MODULE.FailureEvidenceError, "summary_signal_invalid"):
            MODULE.validate_summary(payload)


if __name__ == "__main__":
    unittest.main()
