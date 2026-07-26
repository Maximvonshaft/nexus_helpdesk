from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECK = ROOT / "scripts" / "ci" / "check_value_delivery_governance.py"


def _module():
    spec = importlib.util.spec_from_file_location(
        "nexus_value_delivery_governance_check",
        CHECK,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_five_event_driven_gates_and_golden_journeys_are_consistent():
    assert _module().findings() == []


def test_governance_check_is_part_of_repository_static_authority():
    source = (
        ROOT / "scripts" / "repository_verification_core.py"
    ).read_text(encoding="utf-8")
    assert "scripts/ci/check_value_delivery_governance.py" in source
