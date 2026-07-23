from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in raw:
            raise AssertionError(f"invalid env line {path}:{number}")
        key, value = raw.split("=", 1)
        key = key.strip()
        if key in values:
            raise AssertionError(f"duplicate env key {key} in {path}")
        values[key] = value.strip()
    return values


class ProviderRuntimeDeploymentContractTests(unittest.TestCase):
    def test_current_deployment_profiles_are_explicit_and_fail_closed(self) -> None:
        profiles = (
            "deploy/.env.controlled.example",
            "deploy/.env.controlled.local-postgres.example",
            "deploy/.env.rc-test.example",
        )
        expected = {
            "PROVIDER_RUNTIME_ENABLED": "false",
            "PROVIDER_RUNTIME_TRAFFIC_MODE": "control",
            "PROVIDER_RUNTIME_CANARY_PERCENT": "0",
            "PROVIDER_RUNTIME_KILL_SWITCH": "true",
            "WEBCHAT_AI_ENABLED": "false",
            "ENABLE_OUTBOUND_DISPATCH": "false",
            "OPERATIONS_DISPATCH_MODE": "disabled",
        }
        for relative in profiles:
            values = _read_env(ROOT / relative)
            for key, value in expected.items():
                self.assertEqual(values.get(key), value, f"{relative}:{key}")

    def test_controlled_and_rc_compose_have_distinct_explicit_authorities(self) -> None:
        controlled = (ROOT / "deploy/docker-compose.controlled.yml").read_text(
            encoding="utf-8"
        )
        rc = (ROOT / "deploy/docker-compose.rc-test.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("${CONTROLLED_IMAGE:?", controlled)
        self.assertNotRegex(controlled, r"(?m)^\s*build\s*:")
        self.assertNotIn("env_file:", controlled)
        self.assertIn("DATABASE_URL_APP", controlled)
        self.assertIn("DATABASE_URL_WEBCHAT_AI", controlled)

        self.assertIn("${RC_IMAGE_TAG:?", rc)
        self.assertIn("- .env.rc-test", rc)
        self.assertNotRegex(rc, r"(?m)^\s*build\s*:")
        self.assertIn("postgres-rc:", rc)
        self.assertIn("worker-webchat-ai-rc:", rc)

    def test_rc_generator_and_controlled_preflight_share_fail_closed_mode(
        self,
    ) -> None:
        generator = _load_module(
            "provider_runtime_rc_env",
            ROOT / "scripts/release/generate_rc_test_env.py",
        )
        preflight = _load_module(
            "provider_runtime_controlled_preflight",
            ROOT / "scripts/deploy/validate_controlled_server_preflight.py",
        )
        values = generator.build_values(
            source_sha="a" * 40,
            compose_project="nexus_rc_contract",
            origin="http://127.0.0.1:18083",
            expected_migration_head="contract_head",
        )
        expected = {
            "PROVIDER_RUNTIME_ENABLED": "false",
            "PROVIDER_RUNTIME_TRAFFIC_MODE": "control",
            "PROVIDER_RUNTIME_CANARY_PERCENT": "0",
            "PROVIDER_RUNTIME_KILL_SWITCH": "true",
        }
        for key, value in expected.items():
            self.assertEqual(values[key], value)
            self.assertEqual(preflight.SAFE_CONTROLS[key], value)


if __name__ == "__main__":
    unittest.main()
