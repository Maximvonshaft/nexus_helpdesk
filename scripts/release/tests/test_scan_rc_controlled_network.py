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

    def _safe_network_payload(self, project: str) -> dict[str, object]:
        internal = f"{project}_rc"
        edge = f"{project}_edge"
        return {
            "app_networks": [internal],
            "internal_network": internal,
            "loopback_gateway_network": edge,
            "nginx_networks": [edge, internal],
            "production_network_joined": False,
            "schema": "nexus.osr.rc-test-network-safety.v1",
            "status": "pass",
        }

    def test_exact_controlled_network_names_are_safe_technical_metadata(self) -> None:
        findings, suppressed = self._scan(
            self._safe_network_payload("nexus_controlled_29287748431")
        )
        self.assertEqual(findings, [])
        self.assertGreater(suppressed, 0)

    def test_existing_rc_test_network_names_remain_safe(self) -> None:
        findings, suppressed = self._scan(
            self._safe_network_payload("nexus_rc_test_29287363236")
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
                json.dumps(self._safe_network_payload("nexus_controlled_29287748431")) + "\n",
                encoding="utf-8",
            )
            findings, suppressed = MODULE.scan_rc_artifact_files(root, [relative])
        self.assertEqual(suppressed, 0)
        self.assertIn("artifact:tracking", {finding.rule for finding in findings})


if __name__ == "__main__":
    unittest.main()
