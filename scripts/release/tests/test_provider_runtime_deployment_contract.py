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
    def test_each_deployment_profile_is_explicit_and_fail_closed(self) -> None:
        profiles = (
            "deploy/.env.prod.example",
            "deploy/.env.candidate.example",
            "deploy/.env.controlled.example",
            "deploy/.env.rc-test.example",
        )
        for relative in profiles:
            values = _read_env(ROOT / relative)
            self.assertEqual(
                values.get("PROVIDER_RUNTIME_TRAFFIC_MODE"),
                "control",
                relative,
            )
            self.assertEqual(
                values.get("PROVIDER_RUNTIME_CANARY_PERCENT"),
                "0",
                relative,
            )
            if "PROVIDER_RUNTIME_ENABLED" in values:
                self.assertEqual(
                    values["PROVIDER_RUNTIME_ENABLED"],
                    "false",
                    relative,
                )
            if relative.endswith(("prod.example", "controlled.example", "rc-test.example")):
                self.assertEqual(
                    values.get("PROVIDER_RUNTIME_KILL_SWITCH"),
                    "true",
                    relative,
                )

    def test_compose_profiles_consume_their_single_env_authority(self) -> None:
        expected = {
            "deploy/docker-compose.server.yml": ".env.prod",
            "deploy/docker-compose.candidate.yml": ".env.candidate",
            "deploy/docker-compose.controlled.yml": ".env.controlled",
            "deploy/docker-compose.rc-test.yml": ".env.rc-test",
        }
        for relative, env_name in expected.items():
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn(f"- {env_name}", source, relative)
            self.assertNotIn(
                "PROVIDER_RUNTIME_TRAFFIC_MODE:",
                source,
                relative,
            )

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
        self.assertEqual(
            values["PROVIDER_RUNTIME_TRAFFIC_MODE"],
            "control",
        )
        self.assertEqual(values["PROVIDER_RUNTIME_CANARY_PERCENT"], "0")
        self.assertEqual(values["PROVIDER_RUNTIME_KILL_SWITCH"], "true")
        self.assertEqual(
            preflight.SAFE_CONTROLS["PROVIDER_RUNTIME_TRAFFIC_MODE"],
            "control",
        )
        self.assertEqual(
            preflight.SAFE_CONTROLS["PROVIDER_RUNTIME_CANARY_PERCENT"],
            "0",
        )
        self.assertEqual(
            preflight.SAFE_CONTROLS["PROVIDER_RUNTIME_KILL_SWITCH"],
            "true",
        )


if __name__ == "__main__":
    unittest.main()
