from __future__ import annotations

import json

import pytest

import agent_coordination_open_pr_policy as open_pr_policy


def _manifest(stack_parent: int) -> str:
    payload = {
        "schema": "nexus.osr.coordination.manifest.v1",
        "work_item": 10,
        "agent_run_id": "run-stack",
        "dependency": {
            "mode": "stacked",
            "stack_parent_pr": stack_parent,
        },
        "write_paths": ["backend/target.py"],
        "read_paths": [],
        "contracts": [],
        "database": [],
        "migrations": [],
        "generated_files": [],
        "workflows": [],
    }
    return "Closes #10\n\n```json\n" + json.dumps(payload) + "\n```"


def _snapshot(stack_parent: int) -> dict:
    target_issue = {
        "number": 10,
        "state": "open",
        "labels": ["osr-work-order"],
        "body": (
            "## Control\n"
            "- Lifecycle: In Progress\n"
            "- Current PR: #1\n"
            "- Blocked by: #20\n"
        ),
        "comments": [],
    }
    blocker_issue = {
        "number": 20,
        "state": "open",
        "labels": [],
        "body": (
            "## Control\n"
            "- Lifecycle: In Progress\n"
            "- Current PR: #3\n"
            "- Blocked by: none\n"
        ),
        "comments": [],
    }
    return {
        "pull_request": {
            "number": 1,
            "state": "open",
            "body": _manifest(stack_parent),
            "changed_files": ["backend/target.py"],
        },
        "work_item": target_issue,
        "open_work_items": [target_issue, blocker_issue],
        "open_pull_requests": [],
    }


def test_stack_parent_must_equal_blocker_current_pr() -> None:
    findings = open_pr_policy._stack_parent_authority_findings(_snapshot(2))

    assert [finding.code for finding in findings] == [
        "stack_parent_not_blocker_current_pr"
    ]
    assert findings[0].details == (
        "blocker:20",
        "stack_parent:2",
        "current_pr:3",
    )


def test_authoritative_blocker_current_pr_is_accepted() -> None:
    assert open_pr_policy._stack_parent_authority_findings(_snapshot(3)) == []


def test_closed_current_pr_fails_closed_after_hydration() -> None:
    snapshot = _snapshot(3)
    snapshot["open_pull_requests"] = [
        {
            "number": 1,
            "state": "open",
            "body": _manifest(3),
            "changed_files": [],
        },
        {
            "number": 3,
            "state": "open",
            "body": "Closes #20",
            "changed_files": [],
        },
    ]

    def load_pr(number: int, *, include_files: bool) -> dict:
        assert include_files is True
        if number == 1:
            return {
                "number": 1,
                "state": "open",
                "body": _manifest(3),
                "changed_files": ["backend/target.py"],
            }
        assert number == 3
        return {
            "number": 3,
            "state": "closed",
            "body": "Closes #20",
            "changed_files": ["backend/parent.py"],
        }

    with pytest.raises(
        open_pr_policy.final_policy.model.GateInputError,
        match="current_pr_not_open:pr:3",
    ):
        open_pr_policy._hydrate_current_pr_files(snapshot, load_pr)


def test_entrypoint_installs_final_stack_parent_evaluator() -> None:
    open_pr_policy.install_open_pr_policy()

    assert (
        open_pr_policy.final_policy.policy.gate.evaluate_snapshot
        is open_pr_policy._evaluate_snapshot_policy
    )
