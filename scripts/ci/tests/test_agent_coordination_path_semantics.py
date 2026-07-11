from __future__ import annotations

import copy
import json
from pathlib import Path

import agent_coordination_path_policy as path_policy
import agent_coordination_policy_gate as policy

ROOT = Path(__file__).resolve().parents[3]


def _manifest(work_item: int, run_id: str, *, writes: list[str], reads: list[str] | None = None) -> str:
    payload = {
        "schema": "nexus.osr.coordination.manifest.v1",
        "work_item": work_item,
        "agent_run_id": run_id,
        "dependency": {"mode": "independent", "stack_parent_pr": None},
        "write_paths": writes,
        "read_paths": reads or [],
        "contracts": [],
        "database": [],
        "migrations": [],
        "generated_files": [],
        "workflows": [],
    }
    return f"Closes #{work_item}\n\n```json\n{json.dumps(payload)}\n```"


def _issue(number: int, current_pr: int, run_id: str) -> dict:
    return {
        "number": number,
        "state": "open",
        "labels": ["osr-work-order"],
        "body": (
            "## Control\n"
            "- Lifecycle: In Progress\n"
            f"- Current PR: #{current_pr}\n"
            "- Blocked by: none\n"
        ),
        "comments": [
            {
                "body": f"## AGENT_CLAIM\n- Agent Run ID: `{run_id}`",
                "created_at": "2026-07-11T10:00:00Z",
            }
        ],
    }


def _pr(
    number: int,
    work_item: int,
    run_id: str,
    *,
    writes: list[str],
    changed: list[str],
    reads: list[str] | None = None,
) -> dict:
    return {
        "number": number,
        "state": "open",
        "draft": False,
        "body": _manifest(work_item, run_id, writes=writes, reads=reads),
        "head_sha": str(number) * 40,
        "head_ref": f"branch-{number}",
        "base_ref": "main",
        "created_at": "2026-07-11T10:05:00Z",
        "updated_at": "2026-07-11T10:10:00Z",
        "changed_files": changed,
    }


def _snapshot(
    *,
    target_writes: list[str],
    target_changed: list[str],
    other_writes: list[str],
    other_changed: list[str],
    target_reads: list[str] | None = None,
) -> dict:
    target_issue = _issue(10, 1, "run-target")
    other_issue = _issue(11, 2, "run-other")
    target_pr = _pr(
        1,
        10,
        "run-target",
        writes=target_writes,
        reads=target_reads,
        changed=target_changed,
    )
    other_pr = _pr(
        2,
        11,
        "run-other",
        writes=other_writes,
        changed=other_changed,
    )
    return {
        "schema": "nexus.osr.agent_coordination.snapshot.v1",
        "now": "2026-07-11T10:30:00Z",
        "repository": "Maximvonshaft/nexus_helpdesk",
        "event_action": "synchronize",
        "pull_request": target_pr,
        "work_item": target_issue,
        "open_work_items": [copy.deepcopy(target_issue), other_issue],
        "open_pull_requests": [copy.deepcopy(target_pr), other_pr],
        "blocker_states": {},
    }


def test_single_segment_glob_never_consumes_a_directory_separator() -> None:
    assert path_policy._path_matches("backend/root.py", "backend/*.py")
    assert not path_policy._path_matches(
        "backend/app/settings.py",
        "backend/*.py",
    )
    assert path_policy._path_matches(
        "backend/app/settings.py",
        "backend/**/*.py",
    )


def test_trailing_slash_is_a_recursive_directory_scope() -> None:
    assert path_policy._path_matches("backend/app/settings.py", "backend/")
    assert path_policy._path_specs_overlap("backend/", "backend/app.py")
    assert path_policy._path_specs_overlap("backend/", "backend/**/*.py")
    assert not path_policy._path_specs_overlap("backend/*.py", "backend/app/settings.py")


def test_trailing_directory_write_scope_produces_review_warning() -> None:
    report = policy._evaluate_snapshot_policy(
        _snapshot(
            target_writes=["backend/"],
            target_changed=["backend/a.py"],
            other_writes=["backend/sub/b.py"],
            other_changed=["backend/sub/b.py"],
        )
    )
    assert report["state"] == "warn", report
    assert "broad_write_path_overlap" in set(report["reason_codes"])
    assert "exclusive_write_path_conflict" not in set(report["reason_codes"])


def test_trailing_directory_read_scope_produces_review_warning() -> None:
    report = policy._evaluate_snapshot_policy(
        _snapshot(
            target_writes=["docs/a.md"],
            target_changed=["docs/a.md"],
            target_reads=["backend/"],
            other_writes=["backend/sub/b.py"],
            other_changed=["backend/sub/b.py"],
        )
    )
    assert report["state"] == "warn", report
    assert "read_write_path_overlap" in set(report["reason_codes"])


def test_delivery_protocol_documentation_requires_exact_head() -> None:
    text = (
        ROOT / "docs" / "governance" / "nexus-osr-agent-coordination-gate.md"
    ).read_text(encoding="utf-8")
    delivery_block = text.split("## AGENT_DELIVERY", 1)[1].split("```", 1)[0]
    assert "Exact head" in delivery_block
