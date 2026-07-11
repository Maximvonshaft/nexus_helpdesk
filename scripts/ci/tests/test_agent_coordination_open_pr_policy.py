from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import agent_coordination_entrypoint as entrypoint
import agent_coordination_open_pr_policy as open_pr_policy

FIXTURE = Path(__file__).with_name("fixtures") / "agent_coordination_snapshot.json"


def _manifest(stack_parent: int, *, run_id: str = "run-target") -> str:
    payload = {
        "schema": "nexus.osr.coordination.manifest.v1",
        "work_item": 10,
        "agent_run_id": run_id,
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


def _authority_snapshot(stack_parent: int) -> dict:
    snapshot = json.loads(FIXTURE.read_text(encoding="utf-8"))
    snapshot["event_action"] = "synchronize"

    target_issue = snapshot["work_item"]
    target_issue["body"] = (
        "## Control\n"
        "- Parent Epic: #1\n"
        "- Lifecycle: In Progress\n"
        "- Owner: @owner\n"
        "- Current PR: #101\n"
        "- Blocked by: #20\n"
        "- Supersedes: none\n"
    )
    blocker_issue = {
        "number": 20,
        "state": "open",
        "labels": [],
        "body": (
            "## Control\n"
            "- Lifecycle: In Progress\n"
            "- Current PR: #303\n"
            "- Blocked by: none\n"
        ),
        "comments": [],
    }
    snapshot["open_work_items"] = [copy.deepcopy(target_issue), blocker_issue]

    target_pr = snapshot["pull_request"]
    target_pr.update(
        {
            "body": _manifest(stack_parent),
            "base_ref": "agent/stale" if stack_parent == 202 else "agent/authority",
            "changed_files": ["backend/target.py"],
        }
    )
    stale_parent = {
        "number": 202,
        "state": "open",
        "draft": True,
        "body": "Closes #20",
        "head_sha": "head-stale",
        "head_ref": "agent/stale",
        "base_ref": "main",
        "created_at": "2026-07-10T11:00:00Z",
        "changed_files": ["backend/stale.py"],
    }
    authority_parent = {
        "number": 303,
        "state": "open",
        "draft": True,
        "body": "Closes #20",
        "head_sha": "head-authority",
        "head_ref": "agent/authority",
        "base_ref": "main",
        "created_at": "2026-07-10T11:00:00Z",
        "changed_files": ["backend/authority.py"],
    }
    snapshot["open_pull_requests"] = [
        copy.deepcopy(target_pr),
        stale_parent,
        authority_parent,
    ]
    return snapshot


def test_stale_closes_pr_cannot_be_stack_parent_authority() -> None:
    report = open_pr_policy._evaluate_snapshot_policy(_authority_snapshot(202))

    assert report["state"] == "fail", report
    assert "stack_parent_not_blocker_current_pr" in set(report["reason_codes"])


def test_authoritative_blocker_current_pr_is_accepted() -> None:
    report = open_pr_policy._evaluate_snapshot_policy(_authority_snapshot(303))

    assert "stack_parent_not_blocker_current_pr" not in set(report["reason_codes"])


def test_closed_current_pr_fails_closed_after_hydration() -> None:
    snapshot = _authority_snapshot(303)

    def load_pr(number: int, *, include_files: bool) -> dict:
        assert include_files is True
        for pr in snapshot["open_pull_requests"]:
            if pr["number"] == number:
                result = copy.deepcopy(pr)
                if number == 303:
                    result["state"] = "closed"
                return result
        raise AssertionError(number)

    with pytest.raises(
        open_pr_policy.final_policy.model.GateInputError,
        match="current_pr_not_open:pr:303",
    ):
        open_pr_policy._hydrate_current_pr_files(snapshot, load_pr)


def test_trusted_entrypoint_explicitly_installs_final_stack_parent_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {"install_calls": 0}
    real_install = open_pr_policy.install_open_pr_policy

    def recording_install() -> None:
        observed["install_calls"] = int(observed["install_calls"]) + 1
        real_install()

    def fake_gate_main(argv):
        observed["argv"] = argv
        observed["evaluate"] = open_pr_policy.final_policy.policy.gate.evaluate_snapshot
        return 0

    monkeypatch.setattr(open_pr_policy, "install_open_pr_policy", recording_install)
    monkeypatch.setattr(open_pr_policy.final_policy.policy.gate, "main", fake_gate_main)

    assert entrypoint.main(["--snapshot", str(FIXTURE)]) == 0
    assert observed["install_calls"] == 1
    assert observed["evaluate"] is open_pr_policy._evaluate_snapshot_policy
