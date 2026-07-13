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


class ControlledNetworkScannerTests(unittest.TestCase):
    def _scan(self, payload: dict[str, object]):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = "artifacts/rc-test/network-safety.json"
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            return MODULE.scan_rc_artifact_files(root, [relative])

    def test_exact_controlled_network_names_are_safe_technical_metadata(self) -> None:
        findings, suppressed = self._scan(
            {
                "app_networks": ["nexus_controlled_29287748431_rc"],
                "nginx_networks": [
                    "nexus_controlled_29287748431_edge",
                    "nexus_controlled_29287748431_rc",
                ],
                "internal_network": "nexus_controlled_29287748431_rc",
                "loopback_gateway_network": "nexus_controlled_29287748431_edge",
            }
        )
        self.assertEqual(findings, [])
        self.assertGreater(suppressed, 0)

    def test_existing_rc_test_network_names_remain_safe(self) -> None:
        findings, suppressed = self._scan(
            {
                "internal_network": "nexus_rc_test_29287363236_rc",
                "loopback_gateway_network": "nexus_rc_test_29287363236_edge",
            }
        )
        self.assertEqual(findings, [])
        self.assertGreater(suppressed, 0)

    def test_malformed_controlled_network_name_is_not_suppressed(self) -> None:
        findings, suppressed = self._scan(
            {"internal_network": "nexus_controlled_customer_29287748431_rc"}
        )
        self.assertEqual(suppressed, 0)
        self.assertIn("artifact:tracking", {finding.rule for finding in findings})

    def test_scope_remains_limited_to_rc_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = "other/network-safety.json"
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps({"internal_network": "nexus_controlled_29287748431_rc"}) + "\n",
                encoding="utf-8",
            )
            findings, suppressed = MODULE.scan_rc_artifact_files(root, [relative])
        self.assertEqual(suppressed, 0)
        self.assertIn("artifact:tracking", {finding.rule for finding in findings})


if __name__ == "__main__":
    unittest.main()
