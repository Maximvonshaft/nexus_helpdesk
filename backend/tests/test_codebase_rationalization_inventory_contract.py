from __future__ import annotations

import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from repository_verification_core import (  # noqa: E402
    APPROVED_WORKFLOWS,
    RETIRED_GOVERNANCE_PATHS,
    RETIRED_PATHS,
)

INVENTORY = ROOT / "docs" / "ai" / "codebase-rationalization-inventory.v2.yaml"
WORKFLOW_DIR = ROOT / ".github" / "workflows"


def _inventory() -> dict:
    payload = yaml.safe_load(INVENTORY.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_human_authority_inventory_matches_machine_enforced_workflow_set() -> None:
    inventory = _inventory()
    authorities = inventory["canonical_authorities"]
    actual_workflows = sorted(
        path.relative_to(ROOT).as_posix()
        for path in WORKFLOW_DIR.iterdir()
        if path.is_file()
    )

    assert actual_workflows == APPROVED_WORKFLOWS
    assert authorities["canonical_acceptance"] == APPROVED_WORKFLOWS[0]
    assert authorities["controlled_candidate_convergence"] == APPROVED_WORKFLOWS[1]

    rules = set(inventory["single_authority_rules"])
    assert "one GitHub Actions workflow and one required gate" not in rules
    assert "one Canonical Acceptance workflow for repository verification" in rules
    assert (
        "one downstream Controlled Candidate workflow that consumes successful exact-main acceptance only"
        in rules
    )
    assert (
        "one required-gate for branch protection; internal jobs must not become parallel required authorities"
        in rules
    )
    assert (
        "no independently dispatchable second release or candidate workflow"
        in rules
    )


def test_human_retirement_inventory_covers_machine_enforced_absence_set() -> None:
    inventory = _inventory()
    documented = set(inventory["retired_paths_must_be_absent"])
    machine_enforced = set(RETIRED_PATHS) | set(RETIRED_GOVERNANCE_PATHS)

    assert machine_enforced <= documented
    assert not any((ROOT / path).exists() for path in documented)


def test_controlled_candidate_is_explicitly_protected_as_non_duplicate() -> None:
    inventory = _inventory()
    protected = inventory["protected_non_duplicates"]
    controlled = protected["controlled_candidate_workflow"]

    assert controlled["path"] == APPROVED_WORKFLOWS[1]
    assert "not a second repository acceptance authority" in controlled["reason"]
    assert (
        inventory["verification"]["controlled_candidate"]
        == "automatic only after successful exact-main Canonical Acceptance on a main push"
    )
