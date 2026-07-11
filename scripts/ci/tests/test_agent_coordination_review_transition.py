from __future__ import annotations

import copy
import json

import agent_coordination_path_policy as review_policy


def _manifest() -> str:
    payload = {
        "schema": "nexus.osr.coordination.manifest.v1",
        "work_item": 10,
        "agent_run_id": "run-target",
        "dependency": {"mode": "independent", "stack_parent_pr": None},
        "write_paths": ["backend/a.py"],
        "read_paths": [],
        "contracts": [],
        "database": [],
        "migrations": [],
        "generated_files": [],
        "workflows": [],
    }
    return f"Closes #10\n\n```json\n{json.dumps(payload)}\n```"


def _snapshot(*, delivered: bool) -> dict:
    head = "a" * 40
    comments = [
        {
            "body": "## AGENT_CLAIM\n- Agent Run ID: `run-target`",
            "created_at": "2026-07-11T10:00:00Z",
        }
    ]
    if delivered:
        comments.append(
            {
                "body": (
                    "## AGENT_DELIVERY\n"
                    "- Agent Run ID: `run-target`\n"
                    f"- Exact head: `{head}`"
                ),
                "created_at": "2026-07-11T10:10:00Z",
            }
        )
    issue = {
        "number": 10,
        "state": "open",
        "labels": ["osr-work-order"],
        "body": (
            "## Control\n"
            "- Lifecycle: In Review\n"
            "- Current PR: #1\n"
            "- Blocked by: none\n"
        ),
        "comments": comments,
    }
    pr = {
        "number": 1,
        "state": "open",
        "draft": False,
        "body": _manifest(),
        "head_sha": head,
        "head_ref": "agent/target",
        "base_ref": "main",
        "created_at": "2026-07-11T10:05:00Z",
        "updated_at": "2026-07-11T10:15:00Z",
        "changed_files": ["backend/a.py"],
    }
    return {
        "schema": "nexus.osr.agent_coordination.snapshot.v1",
        "now": "2026-07-11T10:20:00Z",
        "repository": "Maximvonshaft/nexus_helpdesk",
        "event_action": "ready_for_review",
        "pull_request": pr,
        "work_item": issue,
        "open_work_items": [copy.deepcopy(issue)],
        "open_pull_requests": [copy.deepcopy(pr)],
        "blocker_states": {},
    }


def test_ready_for_review_rejects_active_claim_without_exact_delivery() -> None:
    report = review_policy._evaluate_snapshot_policy(_snapshot(delivered=False))
    assert report["state"] == "fail", report
    assert "delivery_head_required_for_review" in set(report["reason_codes"])


def test_ready_for_review_accepts_exact_delivered_head() -> None:
    report = review_policy._evaluate_snapshot_policy(_snapshot(delivered=True))
    assert report["state"] == "pass", report
    assert "delivery_head_required_for_review" not in set(report["reason_codes"])
