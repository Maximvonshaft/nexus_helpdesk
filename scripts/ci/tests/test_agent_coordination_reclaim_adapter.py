from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = ROOT / "scripts" / "ci"
sys.path.insert(0, str(SCRIPT_DIR))

import agent_coordination_policy_gate as adapter  # noqa: E402
from agent_coordination_model import GateInputError  # noqa: E402


def _manifest(run_id: str) -> str:
    return f'''Closes #521

```json
{{"schema":"nexus.osr.coordination.manifest.v1","work_item":521,"agent_run_id":"{run_id}","dependency":{{"mode":"independent","stack_parent_pr":null}},"write_paths":["scripts/ci/**"],"read_paths":[],"contracts":[],"database":[],"migrations":[],"generated_files":[],"workflows":[]}}
```
'''


def _snapshot(
    *,
    heading: str,
    comment_run: str,
    manifest_run: str,
    updated_at: str,
    run_label: str = "New Run ID",
) -> dict:
    return {
        "pull_request": {
            "number": 540,
            "body": _manifest(manifest_run),
            "created_at": "2026-07-10T20:00:00Z",
            "updated_at": updated_at,
        },
        "work_item": {
            "number": 521,
            "comments": [
                {
                    "body": f"## {heading}\n- {run_label}: `{comment_run}`",
                    "created_at": "2026-07-11T19:00:00Z",
                }
            ],
        },
    }


def test_valid_reclaim_uses_server_comment_time_for_existing_pr() -> None:
    snapshot = _snapshot(
        heading="AGENT_RECLAIM",
        comment_run="run-new",
        manifest_run="run-new",
        updated_at="2026-07-11T19:05:00Z",
    )

    adjusted = adapter.apply_reclaim_implementation_start(snapshot)

    assert adjusted["pull_request"]["created_at"] == "2026-07-11T19:00:00Z"
    assert adjusted["pull_request"]["implementation_start_authority"] == "agent_reclaim_comment"
    assert snapshot["pull_request"]["created_at"] == "2026-07-10T20:00:00Z"


def test_new_agent_run_id_field_is_accepted() -> None:
    snapshot = _snapshot(
        heading="AGENT_RECLAIM",
        comment_run="run-new",
        manifest_run="run-new",
        updated_at="2026-07-11T19:05:00Z",
        run_label="New Agent Run ID",
    )

    adjusted = adapter.apply_reclaim_implementation_start(snapshot)

    assert adjusted["pull_request"]["created_at"] == "2026-07-11T19:00:00Z"


def test_ordinary_claim_after_pr_creation_is_not_reinterpreted() -> None:
    snapshot = _snapshot(
        heading="AGENT_CLAIM",
        comment_run="run-new",
        manifest_run="run-new",
        updated_at="2026-07-11T19:05:00Z",
    )

    adjusted = adapter.apply_reclaim_implementation_start(snapshot)

    assert adjusted["pull_request"]["created_at"] == "2026-07-10T20:00:00Z"
    assert "implementation_start_authority" not in adjusted["pull_request"]


def test_reclaim_for_another_run_does_not_authorize_manifest_run() -> None:
    snapshot = _snapshot(
        heading="AGENT_RECLAIM",
        comment_run="other-run",
        manifest_run="run-new",
        updated_at="2026-07-11T19:05:00Z",
    )

    adjusted = adapter.apply_reclaim_implementation_start(snapshot)

    assert adjusted["pull_request"]["created_at"] == "2026-07-10T20:00:00Z"


def test_quoted_reclaim_example_is_not_lease_authority() -> None:
    snapshot = _snapshot(
        heading="AGENT_CLAIM",
        comment_run="run-new",
        manifest_run="run-new",
        updated_at="2026-07-11T19:05:00Z",
    )
    snapshot["work_item"]["comments"][0]["body"] = (
        "Example only:\n> ## AGENT_RECLAIM\n> - New Run ID: `run-new`"
    )

    adjusted = adapter.apply_reclaim_implementation_start(snapshot)

    assert adjusted["pull_request"]["created_at"] == "2026-07-10T20:00:00Z"


def test_reclaim_requires_pr_update_after_server_reclaim_comment() -> None:
    snapshot = _snapshot(
        heading="AGENT_RECLAIM",
        comment_run="run-new",
        manifest_run="run-new",
        updated_at="2026-07-11T18:59:59Z",
    )

    with pytest.raises(GateInputError, match="reclaim_not_reflected_in_pr_update"):
        adapter.apply_reclaim_implementation_start(snapshot)


def test_event_updated_at_can_supply_live_github_timestamp() -> None:
    snapshot = _snapshot(
        heading="AGENT_RECLAIM",
        comment_run="run-new",
        manifest_run="run-new",
        updated_at="",
    )

    adjusted = adapter.apply_reclaim_implementation_start(
        snapshot,
        event_updated_at="2026-07-11T19:10:00Z",
    )

    assert adjusted["pull_request"]["created_at"] == "2026-07-11T19:00:00Z"


def test_distinct_long_contract_names_do_not_collapse_during_comparison() -> None:
    left = ["required-model-registration-v1"]
    right = ["agent-coordination-v1"]

    assert adapter._canonical_resource_intersection(left, right) == set()
    assert adapter.core._resource_intersection(left, right) == set()


def test_exact_resource_identity_still_conflicts_case_insensitively() -> None:
    left = ["Agent-Coordination-V1", "none"]
    right = ["agent-coordination-v1", "N/A"]

    assert adapter._canonical_resource_intersection(left, right) == {
        "agent-coordination-v1"
    }
