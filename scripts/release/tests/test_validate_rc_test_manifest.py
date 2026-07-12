from __future__ import annotations

import copy
import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "validate_rc_test_manifest.py"
SPEC = importlib.util.spec_from_file_location("validate_rc_test_manifest", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def valid_manifest():
    return {
        "schema": "nexus.osr.rc-test-candidate.v1",
        "release_class": "controlled_test_deployment",
        "decision": "RC0_TEST_DEPLOYABLE",
        "candidate": {
            "source_sha": "a" * 40,
            "frontend_build_sha": "a" * 40,
            "image_tag": "nexusdesk/helpdesk:rc-test-a",
            "image_id": "sha256:" + "b" * 64,
            "migration_revision": "20260711_0058",
            "config_profile": "rc-test-isolated-v1",
            "config_digest": "sha256:" + "c" * 64,
        },
        "checks": {
            "image_build": "pass",
            "compose_validation": "pass",
            "migration": "pass",
            "application_ready": "pass",
            "workers_healthy": "pass",
            "http_core_smoke": "pass",
            "browser_smoke": "pass",
            "side_effect_safety": "pass",
            "teardown": "pass",
        },
        "safety": {
            "production_data_used": False,
            "production_network_joined": False,
            "provider_candidate_enabled": False,
            "real_outbound_enabled": False,
            "whatsapp_enabled": False,
            "speedaf_write_enabled": False,
            "production_ready": False,
            "full_osr_automation": "NO_GO",
            "test_environment_isolated": True,
        },
        "evidence": {
            "health": "healthz.json",
            "readiness": "readyz.json",
        },
    }


class ManifestValidationTests(unittest.TestCase):
    def test_accepts_complete_isolated_candidate_manifest(self):
        MODULE.validate_manifest(valid_manifest())

    def test_rejects_unsafe_or_incomplete_manifest(self):
        cases = [
            (("decision",), "PRODUCTION_GO"),
            (("candidate", "source_sha"), "bad"),
            (("candidate", "frontend_build_sha"), "b" * 40),
            (("checks", "browser_smoke"), "not_run"),
            (("safety", "real_outbound_enabled"), True),
            (("safety", "production_ready"), True),
            (("safety", "full_osr_automation"), "GO"),
            (("safety", "test_environment_isolated"), False),
        ]
        for path, value in cases:
            with self.subTest(path=path, value=value):
                payload = copy.deepcopy(valid_manifest())
                cursor = payload
                for key in path[:-1]:
                    cursor = cursor[key]
                cursor[path[-1]] = value
                with self.assertRaises(MODULE.ManifestError):
                    MODULE.validate_manifest(payload)

    def test_rejects_absolute_evidence_paths(self):
        payload = valid_manifest()
        payload["evidence"]["health"] = "/tmp/healthz.json"
        with self.assertRaises(MODULE.ManifestError):
            MODULE.validate_manifest(payload)


class TopologyContractTests(unittest.TestCase):
    def test_app_healthcheck_uses_runtime_available_python_client(self):
        root = Path(__file__).resolve().parents[3]
        text = (root / "deploy" / "docker-compose.rc-test.yml").read_text(encoding="utf-8")
        app_block = text.split("  app-rc:\n", 1)[1].split("\n  worker-outbound-rc:\n", 1)[0]
        self.assertNotIn("- curl\n", app_block)
        self.assertIn("urllib.request.urlopen('http://127.0.0.1:8080/readyz', timeout=4).read()", app_block)


if __name__ == "__main__":
    unittest.main()
