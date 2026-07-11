from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = ROOT / "scripts" / "ci"
sys.path.insert(0, str(SCRIPT_DIR))

import agent_coordination_policy_gate as policy  # noqa: E402
from agent_coordination_model import GateInputError, SNAPSHOT_SCHEMA  # noqa: E402

NOW = "2026-07-11T10:30:00Z"


def _manifest(
    work_item: int,
    run_id: str,
    *,
    writes: list[str],
    reads: list[str] | None = None,
) -> str:
    read_json = reads or []
    return f'''Closes #{work_item}

```json
{{"schema":"nexus.osr.coordination.manifest.v1","work_item":{work_item},"agent_run_id":"{run_id}","dependency":{{"mode":"independent","stack_parent_pr":null}},"write_paths":{writes!r},"read_paths":{read_json!r},"contracts":[],"database":[],"migrations":[],"generated_files":[],"workflows":[]}}
```
'''.replace("'", '"')


def _issue(
    number: int,
    current_pr: int,
    *,
    blocked_by: str = "none",
    comments: list[dict] | None = None,
    labels: list[str] | None = None,
    state: str = "open",
) -> dict:
    return {
        "number": number,
        "state": state,
        "labels": labels if labels is not None else ["osr-work-order"],
        "body": (
            "## Control\n"
            "- Lifecycle: In Progress\n"
            f"- Current PR: #{current_pr}\n"
            f"- Blocked by: {blocked_by}\n"
        ),
        "comments": comments or [],
    }


def _claim(run_id: str, at: str = "2026-07-11T10:00:00Z") -> dict:
    return {
        "body": f"## AGENT_CLAIM\n- Agent Run ID: `{run_id}`",
        "created_at": at,
    }


def _delivery(
    run_id: str,
    at: str = "2026-07-11T10:20:00Z",
    *,
    head_sha: str = "1" * 40,
) -> dict:
    return {
        "body": (
            f"## AGENT_DELIVERY\n"
            f"- Agent Run ID: `{run_id}`\n"
            f"- Exact head: `{head_sha}`"
        ),
        "created_at": at,
    }


def _reclaim(run_id: str, at: str = "2026-07-11T12:01:00Z") -> dict:
    return {
        "body": f"## AGENT_RECLAIM\n- New Agent Run ID: `{run_id}`",
        "created_at": at,
    }


def _pr(
    number: int,
    work_item: int,
    run_id: str,
    *,
    writes: list[str],
    reads: list[str] | None = None,
    changed: list[str] | None = None,
    created_at: str = "2026-07-11T10:05:00Z",
    updated_at: str = "2026-07-11T10:10:00Z",
) -> dict:
    return {
        "number": number,
        "state": "open",
        "draft": False,
        "body": _manifest(work_item, run_id, writes=writes, reads=reads),
        "head_sha": str(number) * 40,
        "head_ref": f"branch-{number}",
        "base_ref": "main",
        "created_at": created_at,
        "updated_at": updated_at,
        "changed_files": changed or [],
    }


def _snapshot(
    *,
    event_action: str = "synchronize",
    target_writes: list[str] | None = None,
    target_reads: list[str] | None = None,
    target_changed: list[str] | None = None,
    comments: list[dict] | None = None,
    now: str = NOW,
    other_writes: list[str] | None = None,
    other_reads: list[str] | None = None,
    other_changed: list[str] | None = None,
    blocked_by: str = "none",
) -> dict:
    target_writes = target_writes or ["backend/a.py"]
    target_changed = target_changed or ["backend/a.py"]
    target_comments = comments or [_claim("run-target")]
    target_issue = _issue(10, 1, blocked_by=blocked_by, comments=target_comments)
    target_pr = _pr(
        1,
        10,
        "run-target",
        writes=target_writes,
        reads=target_reads,
        changed=target_changed,
    )
    open_issues = [copy.deepcopy(target_issue)]
    open_prs = [copy.deepcopy(target_pr)]
    if other_writes is not None:
        other_issue = _issue(11, 2)
        other_pr = _pr(
            2,
            11,
            "run-other",
            writes=other_writes,
            reads=other_reads,
            changed=other_changed or [],
        )
        open_issues.append(other_issue)
        open_prs.append(other_pr)
    return {
        "schema": SNAPSHOT_SCHEMA,
        "now": now,
        "repository": "Maximvonshaft/nexus_helpdesk",
        "event_action": event_action,
        "pull_request": target_pr,
        "work_item": target_issue,
        "open_work_items": open_issues,
        "open_pull_requests": open_prs,
        "blocker_states": {},
    }


def _codes(report: dict) -> set[str]:
    return set(report.get("reason_codes") or [])


def test_release_after_creation_preserves_historical_authorization_for_ready_review() -> None:
    snapshot = _snapshot(
        event_action="ready_for_review",
        comments=[_claim("run-target"), _delivery("run-target")],
        now="2026-07-11T10:40:00Z",
    )
    report = policy._evaluate_snapshot_policy(snapshot)
    assert report["state"] == "pass", report
    assert "claim_not_valid_at_pr_creation" not in _codes(report)
    assert "active_claim_missing_or_expired" not in _codes(report)


def test_delivery_for_old_head_cannot_authorize_later_non_writing_event() -> None:
    snapshot = _snapshot(
        event_action="edited",
        comments=[_claim("run-target"), _delivery("run-target")],
        now="2026-07-11T10:40:00Z",
    )
    snapshot["pull_request"]["head_sha"] = "f" * 40
    snapshot["open_pull_requests"][0] = copy.deepcopy(snapshot["pull_request"])

    report = policy._evaluate_snapshot_policy(snapshot)

    assert report["state"] == "fail", report
    assert "active_claim_missing_or_expired" in _codes(report)


def test_delivery_without_exact_head_is_not_review_authority() -> None:
    delivery = _delivery("run-target")
    delivery["body"] = "## AGENT_DELIVERY\n- Agent Run ID: `run-target`"
    snapshot = _snapshot(
        event_action="ready_for_review",
        comments=[_claim("run-target"), delivery],
        now="2026-07-11T10:40:00Z",
    )

    report = policy._evaluate_snapshot_policy(snapshot)

    assert report["state"] == "fail", report
    assert "active_claim_missing_or_expired" in _codes(report)


def test_new_commit_after_delivery_requires_reclaim() -> None:
    snapshot = _snapshot(
        event_action="synchronize",
        comments=[_claim("run-target"), _delivery("run-target")],
        now="2026-07-11T10:40:00Z",
    )
    report = policy._evaluate_snapshot_policy(snapshot)
    assert report["state"] == "fail", report
    assert "active_claim_missing_or_expired" in _codes(report)


def test_expired_undelivered_claim_does_not_remain_review_authority() -> None:
    snapshot = _snapshot(
        event_action="ready_for_review",
        comments=[_claim("run-target")],
        now="2026-07-11T13:00:00Z",
    )
    report = policy._evaluate_snapshot_policy(snapshot)
    assert report["state"] == "fail", report
    assert "active_claim_missing_or_expired" in _codes(report)


def test_valid_reclaim_allows_resumed_existing_pr_write() -> None:
    snapshot = _snapshot(
        comments=[_claim("old-run"), _reclaim("run-target")],
        now="2026-07-11T12:30:00Z",
    )
    snapshot["pull_request"]["created_at"] = "2026-07-11T10:05:00Z"
    snapshot["pull_request"]["updated_at"] = "2026-07-11T12:05:00Z"
    snapshot["open_pull_requests"][0] = copy.deepcopy(snapshot["pull_request"])
    adjusted = policy.apply_reclaim_implementation_start(snapshot)
    report = policy._evaluate_snapshot_policy(adjusted)
    assert adjusted["pull_request"]["created_at"] == "2026-07-11T12:01:00Z"
    assert report["state"] == "pass", report


def test_open_non_work_order_blocker_is_hydrated_and_blocks() -> None:
    snapshot = _snapshot(blocked_by="#900")
    adjusted = policy.hydrate_blocker_authority(
        snapshot,
        lambda number: _issue(number, 99, labels=["security-control"]),
    )
    report = policy._evaluate_snapshot_policy(adjusted)
    assert report["state"] == "fail", report
    assert "unmet_blockers" in _codes(report)


def test_closed_blocker_is_resolved_regardless_of_label() -> None:
    snapshot = _snapshot(blocked_by="#900")
    adjusted = policy.hydrate_blocker_authority(
        snapshot,
        lambda number: _issue(number, 99, labels=["security-control"], state="closed"),
    )
    report = policy._evaluate_snapshot_policy(adjusted)
    assert report["state"] == "pass", report
    assert "unmet_blockers" not in _codes(report)


def test_unavailable_blocker_lookup_fails_closed() -> None:
    snapshot = _snapshot(blocked_by="#900")

    def unavailable(_number: int) -> dict:
        raise RuntimeError("network details must not escape")

    with pytest.raises(GateInputError, match="blocker_lookup_unavailable"):
        policy.hydrate_blocker_authority(snapshot, unavailable)


def test_exact_write_write_collision_blocks() -> None:
    snapshot = _snapshot(
        target_writes=["backend/shared.py"],
        target_changed=["backend/shared.py"],
        other_writes=["backend/shared.py"],
        other_changed=["backend/shared.py"],
    )
    report = policy._evaluate_snapshot_policy(snapshot)
    assert report["state"] == "fail", report
    assert "exclusive_write_path_conflict" in _codes(report)


def test_broad_write_overlap_warns_without_exact_collision() -> None:
    snapshot = _snapshot(
        target_writes=["backend/**"],
        target_changed=["backend/a.py"],
        other_writes=["backend/**"],
        other_changed=["backend/b.py"],
    )
    report = policy._evaluate_snapshot_policy(snapshot)
    assert report["state"] == "warn", report
    assert "broad_write_path_overlap" in _codes(report)
    assert "exclusive_write_path_conflict" not in _codes(report)


def test_same_file_glob_overlap_warns_without_actual_collision() -> None:
    snapshot = _snapshot(
        target_writes=["backend/*.py"],
        target_changed=["backend/a.py"],
        other_writes=["backend/*.py"],
        other_changed=["backend/b.py"],
    )
    report = policy._evaluate_snapshot_policy(snapshot)
    assert report["state"] == "warn", report
    assert "broad_write_path_overlap" in _codes(report)
    assert "exclusive_write_path_conflict" not in _codes(report)


def test_specific_different_globs_with_concrete_filename_intersection_block() -> None:
    snapshot = _snapshot(
        target_writes=["services/*/config.yml"],
        target_changed=["services/web/config.yml"],
        other_writes=["services/api/*.yml"],
        other_changed=["services/api/other.yml"],
    )
    report = policy._evaluate_snapshot_policy(snapshot)
    assert report["state"] == "fail", report
    assert "exclusive_write_path_conflict" in _codes(report)


def test_read_write_overlap_warns() -> None:
    snapshot = _snapshot(
        target_writes=["docs/a.md"],
        target_reads=["backend/config/**"],
        target_changed=["docs/a.md"],
        other_writes=["backend/config/settings.py"],
        other_changed=["backend/config/settings.py"],
    )
    report = policy._evaluate_snapshot_policy(snapshot)
    assert report["state"] == "warn", report
    assert "read_write_path_overlap" in _codes(report)


def test_disjoint_resources_pass() -> None:
    snapshot = _snapshot(
        target_writes=["docs/a.md"],
        target_reads=["backend/contracts/**"],
        target_changed=["docs/a.md"],
        other_writes=["webapp/src/a.ts"],
        other_reads=["webapp/contracts/**"],
        other_changed=["webapp/src/a.ts"],
    )
    report = policy._evaluate_snapshot_policy(snapshot)
    assert report["state"] == "pass", report
