from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


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
