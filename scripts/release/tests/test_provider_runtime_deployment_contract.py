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
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
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
    def test_supported_environment_profiles_are_explicit_and_fail_closed(self) -> None:
        profiles = (
            "deploy/.env.controlled.example",
            "deploy/.env.controlled.local-postgres.example",
            "deploy/.env.rc-test.example",
        )
        for relative in profiles:
            values = _read_env(ROOT / relative)
            self.assertEqual(values.get("PROVIDER_RUNTIME_ENABLED"), "false", relative)
            self.assertEqual(values.get("PROVIDER_RUNTIME_TRAFFIC_MODE"), "control", relative)
            self.assertEqual(values.get("PROVIDER_RUNTIME_CANARY_PERCENT"), "0", relative)
            self.assertEqual(values.get("PROVIDER_RUNTIME_KILL_SWITCH"), "true", relative)

    def test_retired_parallel_deployment_profiles_remain_absent(self) -> None:
        for relative in (
            "deploy/.env.prod.example",
            "deploy/.env.candidate.example",
            "deploy/docker-compose.server.yml",
            "deploy/docker-compose.candidate.yml",
        ):
            self.assertFalse((ROOT / relative).exists(), relative)

    def test_controlled_compose_owns_fail_closed_runtime_interpolation(self) -> None:
        source = (ROOT / "deploy/docker-compose.controlled.yml").read_text(encoding="utf-8")
        self.assertNotIn("env_file:", source)
        for marker in (
            "PROVIDER_RUNTIME_ENABLED: ${PROVIDER_RUNTIME_ENABLED:-false}",
            "PROVIDER_RUNTIME_TRAFFIC_MODE: ${PROVIDER_RUNTIME_TRAFFIC_MODE:-control}",
            "PROVIDER_RUNTIME_KILL_SWITCH: ${PROVIDER_RUNTIME_KILL_SWITCH:-true}",
            "PROVIDER_RUNTIME_CANARY_PERCENT: ${PROVIDER_RUNTIME_CANARY_PERCENT:-0}",
            'ENABLE_OUTBOUND_DISPATCH: "false"',
            'WHATSAPP_NATIVE_ENABLED: "false"',
            'SPEEDAF_WORK_ORDER_CREATE_ENABLED: "false"',
            "OPERATIONS_DISPATCH_MODE: disabled",
        ):
            self.assertIn(marker, source)

    def test_rc_compose_consumes_only_the_generated_rc_environment(self) -> None:
        source = (ROOT / "deploy/docker-compose.rc-test.yml").read_text(encoding="utf-8")
        self.assertIn("env_file:\n    - .env.rc-test", source)
        self.assertNotIn("PROVIDER_RUNTIME_TRAFFIC_MODE:", source)
        self.assertNotIn("PROVIDER_RUNTIME_KILL_SWITCH:", source)

    def test_rc_generator_and_controlled_preflight_share_fail_closed_mode(self) -> None:
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
