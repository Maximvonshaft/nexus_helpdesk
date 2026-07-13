from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scan_rc_test_artifacts.py"
SPEC = importlib.util.spec_from_file_location("scan_rc_test_artifacts", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RcArtifactScannerTests(unittest.TestCase):
    def _write(self, root: Path, payload: object, *, name: str = "evidence.json") -> str:
        relative = f"artifacts/rc-test/{name}"
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        return relative

    def test_strict_synthetic_metadata_suppresses_only_technical_pii_fingerprints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            relative = self._write(
                root,
                {
                    "origin": "http://127.0.0.1:18083",
                    "page_url": "http://127.0.0.1:18083/webchat/demo/",
                    "conversation_id": "wc_1234abcd5678efgh",
                    "app_networks": ["nexus_rc_test_29199983935_rc"],
                    "nginx_networks": [
                        "nexus_rc_test_29199983935_edge",
                        "nexus_rc_test_29199983935_rc",
                    ],
                    "app_version": "rc-test-17cd31ad15f3",
                    "image_tag": "nexusdesk/helpdesk:rc-test-" + "a1b2" * 10,
                    "build_time": "20260712T161615Z",
                    "migration_revision": "20260711_0058",
                },
            )

            findings, suppressed = MODULE.scan_rc_artifact_files(root, [relative])

            self.assertEqual(findings, [])
            self.assertGreater(suppressed, 0)

    def test_external_or_malformed_values_are_not_suppressed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            relative = self._write(
                root,
                {
                    "origin": "https://person@example.com",
                    "conversation_id": "TRACK1234567890",
                    "internal_network": "customer-1234567890",
                },
            )

            findings, suppressed = MODULE.scan_rc_artifact_files(root, [relative])
            rules = {finding.rule for finding in findings}

            self.assertEqual(suppressed, 0)
            self.assertIn("artifact:email", rules)
            self.assertIn("artifact:tracking", rules)

    def test_secret_finding_is_never_suppressed_by_technical_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            token = "sk-proj-" + "A" * 36
            relative = self._write(root, {"image_tag": token})

            findings, suppressed = MODULE.scan_rc_artifact_files(root, [relative])

            self.assertEqual(suppressed, 0)
            self.assertIn("artifact:openai_key", {finding.rule for finding in findings})

    def test_scope_is_limited_to_rc_evidence_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            relative = "other/evidence.json"
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"origin": "http://127.0.0.1:18083"}), encoding="utf-8")

            findings, suppressed = MODULE.scan_rc_artifact_files(root, [relative])

            self.assertEqual(suppressed, 0)
            self.assertIn("artifact:phone", {finding.rule for finding in findings})


if __name__ == "__main__":
    unittest.main()
