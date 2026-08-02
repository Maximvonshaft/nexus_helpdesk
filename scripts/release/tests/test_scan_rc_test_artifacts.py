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

    def _network_payload(self, *, project: str = "nexus_pr912_rc_30732266088") -> dict[str, object]:
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

    def test_strict_synthetic_metadata_suppresses_only_technical_pii_fingerprints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            readyz = self._write(
                root,
                {
                    "origin": "http://127.0.0.1:18083",
                    "page_url": "http://127.0.0.1:18083/webchat/demo/",
                    "conversation_id": "wc_1234abcd5678efgh",
                    "app_version": "rc-test-17cd31ad15f3",
                    "image_tag": "nexusdesk/helpdesk:rc-test-" + "a1b2" * 10,
                    "build_time": "20260712T161615Z",
                    "migration_revision": "20260729_r15_tenant_scope",
                    "migration": {
                        "expected": "20260729_r15_tenant_scope",
                        "observed": "20260729_r15_tenant_scope",
                    },
                },
                name="readyz.json",
            )
            network = self._write(
                root,
                self._network_payload(),
                name="network-safety.json",
            )

            findings, suppressed = MODULE.scan_rc_artifact_files(root, [readyz, network])

            self.assertEqual(findings, [])
            self.assertGreater(suppressed, 0)

    def test_controlled_and_pr_witness_network_names_are_validated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = []
            for index, project in enumerate(
                (
                    "nexus_rc_test_29199983935",
                    "nexus_controlled_29199983935",
                    "nexus_pr912_rc_30732266088",
                )
            ):
                paths.append(
                    self._write(
                        root,
                        self._network_payload(project=project),
                        name="network-safety.json" if index == 0 else f"network-safety-{index}.json",
                    )
                )

            canonical_findings, canonical_suppressed = MODULE.scan_rc_artifact_files(root, [paths[0]])
            self.assertEqual(canonical_findings, [])
            self.assertGreater(canonical_suppressed, 0)

            for noncanonical_path in paths[1:]:
                findings, suppressed = MODULE.scan_rc_artifact_files(root, [noncanonical_path])
                self.assertEqual(suppressed, 0)
                self.assertIn("artifact:tracking", {finding.rule for finding in findings})

            for project in ("nexus_controlled_29199983935", "nexus_pr912_rc_30732266088"):
                relative = self._write(
                    root,
                    self._network_payload(project=project),
                    name="network-safety.json",
                )
                findings, suppressed = MODULE.scan_rc_artifact_files(root, [relative])
                self.assertEqual(findings, [])
                self.assertGreater(suppressed, 0)

    def test_network_suppression_requires_complete_safe_topology(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unsafe_cases = []

            production_joined = self._network_payload()
            production_joined["production_network_joined"] = True
            unsafe_cases.append(production_joined)

            mismatched = self._network_payload()
            mismatched["nginx_networks"] = [
                "nexus_pr912_rc_30732266088_edge",
                "nexus_pr913_rc_30732266088_rc",
            ]
            unsafe_cases.append(mismatched)

            malformed = self._network_payload()
            malformed["internal_network"] = "customer-TRACK1234567890"
            malformed["app_networks"] = ["customer-TRACK1234567890"]
            unsafe_cases.append(malformed)

            for payload in unsafe_cases:
                relative = self._write(root, payload, name="network-safety.json")
                findings, suppressed = MODULE.scan_rc_artifact_files(root, [relative])
                self.assertEqual(suppressed, 0)
                self.assertIn("artifact:tracking", {finding.rule for finding in findings})

    def test_canonical_migration_suppression_is_limited_to_exact_rc_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical = "20260729_r15_tenant_scope"
            candidate = self._write(
                root,
                {"candidate": {"migration_revision": canonical}},
                name="candidate-manifest.json",
            )
            unrelated = self._write(
                root,
                {"migration_revision": canonical},
                name="unrelated.json",
            )

            candidate_findings, suppressed = MODULE.scan_rc_artifact_files(
                root,
                [candidate],
            )
            unrelated_findings, unrelated_suppressed = MODULE.scan_rc_artifact_files(
                root,
                [unrelated],
            )

            self.assertEqual(candidate_findings, [])
            self.assertGreater(suppressed, 0)
            self.assertEqual(unrelated_suppressed, 0)
            self.assertIn(
                "artifact:tracking",
                {finding.rule for finding in unrelated_findings},
            )

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
