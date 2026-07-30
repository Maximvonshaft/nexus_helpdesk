from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
ACTIVATION_COMPOSE = ROOT / "deploy/docker-compose.production-activation.yml"


def _load_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


deployment = _load_module(
    "nexus_production_operational_authority",
    "scripts/qualification/deployment_authority.py",
)
supply_chain = _load_module(
    "nexus_production_supply_chain",
    "scripts/qualification/supply_chain.py",
)


def test_current_production_operational_authority_is_clean() -> None:
    assert deployment.deployment_authority_findings(ROOT) == []


def test_retired_operational_paths_cannot_return() -> None:
    for relative in deployment.RETIRED_DEPLOY_PATHS:
        assert not (ROOT / relative).exists(), relative


def test_current_operational_shell_entrypoints_parse() -> None:
    for relative in (
        "scripts/probe_nexus_runtime.sh",
        "scripts/smoke/runtime_performance_baseline.sh",
        "scripts/deploy/rollback_release.sh",
    ):
        completed = subprocess.run(
            ["bash", "-n", str(ROOT / relative)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, (
            relative,
            completed.stdout,
            completed.stderr,
        )


def test_retired_env_pattern_does_not_reject_activation_environment() -> None:
    current = "deploy/.env.production-activation"
    retired = "deploy/.env.prod"
    assert all(
        re.search(pattern, current) is None
        for pattern in deployment.RETIRED_OPERATIONAL_PATTERNS
    )
    assert any(
        re.search(pattern, retired) is not None
        for pattern in deployment.RETIRED_OPERATIONAL_PATTERNS
    )


def test_operational_authorities_are_release_evidence_inputs() -> None:
    required = {
        "scripts/probe_nexus_runtime.sh",
        "scripts/smoke/runtime_performance_baseline.sh",
        "scripts/deploy/rollback_release.sh",
        "scripts/release_metadata_consistency_gate.py",
        "docs/runbooks/production-activation.md",
        "docs/runbooks/outbound-email-production-pilot.md",
    }
    assert required <= set(supply_chain.SUPPLY_CHAIN_INPUTS)


def _activation_services() -> dict:
    payload = yaml.safe_load(ACTIVATION_COMPOSE.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    services = payload.get("services")
    assert isinstance(services, dict)
    return services


def test_email_pilot_flag_reaches_preflight_app_and_outbound_worker() -> None:
    services = _activation_services()
    for service_name in (
        "production-activation-preflight",
        "app-controlled",
        "worker-outbound-controlled",
    ):
        environment = services[service_name].get("environment") or {}
        assert "OUTBOUND_EMAIL_PRODUCTION_PILOT_ENABLED" in environment


def test_mailbox_sync_is_visible_only_to_fail_closed_preflight() -> None:
    services = _activation_services()
    preflight_environment = (
        services["production-activation-preflight"].get("environment") or {}
    )
    assert "EMAIL_MAILBOX_SYNC_ENABLED" in preflight_environment
    for service_name in (
        "app-controlled",
        "worker-background-controlled",
    ):
        environment = services[service_name].get("environment") or {}
        assert "EMAIL_MAILBOX_SYNC_ENABLED" not in environment


def test_external_effect_processes_wait_for_activation_preflight() -> None:
    services = _activation_services()
    for service_name in (
        "app-controlled",
        "worker-outbound-controlled",
        "worker-background-controlled",
        "worker-webchat-ai-controlled",
        "whatsapp-sidecar-controlled",
    ):
        depends_on = services[service_name].get("depends_on") or {}
        preflight = depends_on.get("production-activation-preflight") or {}
        assert preflight.get("condition") == "service_completed_successfully"
