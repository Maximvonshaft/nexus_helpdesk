from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bind_release_image_compliance import BindingError, bind  # noqa: E402


class BindReleaseImageComplianceTests(unittest.TestCase):
    def _write(self, root: Path, name: str, payload) -> Path:
        path = root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _inputs(self, root: Path) -> dict[str, Path]:
        source = "a" * 40
        image = "sha256:" + ("b" * 64)
        return {
            "manifest": self._write(
                root,
                "manifest.json",
                {
                    "schema_version": "nexus_release_image_assurance_v1",
                    "status": "pass",
                    "source_sha": source,
                    "image_id": image,
                },
            ),
            "policy": self._write(
                root,
                "policy.json",
                {
                    "schema_version": "nexus_release_image_policy_input_validation_v1",
                    "status": "pass",
                    "evaluated_on": "2026-07-12",
                },
            ),
            "compliance": self._write(
                root,
                "compliance.json",
                {
                    "schema_version": "nexus_container_license_compliance_evidence_v1",
                    "status": "pass",
                },
            ),
            "installed": self._write(
                root,
                "installed.json",
                {
                    "schema_version": "nexus_installed_license_evidence_v1",
                    "components": [],
                },
            ),
        }

    def test_binding_covers_policy_validation_and_date(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._inputs(root)
            output = root / "binding.json"

            self.assertEqual(
                bind(
                    source_sha="a" * 40,
                    image_id="sha256:" + ("b" * 64),
                    manifest_path=paths["manifest"],
                    policy_input_validation_path=paths["policy"],
                    compliance_path=paths["compliance"],
                    installed_path=paths["installed"],
                    output_path=output,
                ),
                0,
            )
            payload = json.loads(output.read_text())
            self.assertEqual(payload["status"], "pass")
            self.assertEqual(payload["evaluated_on"], "2026-07-12")
            self.assertTrue(
                payload["policy_input_validation_sha256"].startswith("sha256:")
            )
            self.assertFalse(payload["image_pushed"])
            self.assertFalse(payload["deployment_performed"])

    def test_failed_policy_validation_cannot_produce_passing_binding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._inputs(root)
            paths["policy"] = self._write(
                root,
                "policy.json",
                {
                    "schema_version": "nexus_release_image_policy_input_validation_v1",
                    "status": "fail",
                    "evaluated_on": "2026-07-12",
                },
            )
            output = root / "binding.json"

            self.assertEqual(
                bind(
                    source_sha="a" * 40,
                    image_id="sha256:" + ("b" * 64),
                    manifest_path=paths["manifest"],
                    policy_input_validation_path=paths["policy"],
                    compliance_path=paths["compliance"],
                    installed_path=paths["installed"],
                    output_path=output,
                ),
                1,
            )
            self.assertEqual(json.loads(output.read_text())["status"], "fail")

    def test_invalid_evaluation_date_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._inputs(root)
            paths["policy"] = self._write(
                root,
                "policy.json",
                {
                    "schema_version": "nexus_release_image_policy_input_validation_v1",
                    "status": "pass",
                    "evaluated_on": "not-a-date",
                },
            )

            with self.assertRaisesRegex(BindingError, "policy_evaluated_on_invalid"):
                bind(
                    source_sha="a" * 40,
                    image_id="sha256:" + ("b" * 64),
                    manifest_path=paths["manifest"],
                    policy_input_validation_path=paths["policy"],
                    compliance_path=paths["compliance"],
                    installed_path=paths["installed"],
                    output_path=root / "binding.json",
                )


if __name__ == "__main__":
    unittest.main()
