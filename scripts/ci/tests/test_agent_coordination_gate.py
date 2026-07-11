from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
GATE_SCRIPT = ROOT / "scripts" / "ci" / "agent_coordination_gate.py"
SCRIPT = ROOT / "scripts" / "ci" / "agent_coordination_policy_gate.py"
sys.path.insert(0, str(SCRIPT.parent))
FIXTURE = Path(__file__).with_name("fixtures") / "agent_coordination_snapshot.json"
WORKFLOW = ROOT / ".github" / "workflows" / "agent-coordination-gate.yml"
SELF_TEST_WORKFLOW = ROOT / ".github" / "workflows" / "agent-coordination-self-test.yml"

spec = importlib.util.spec_from_file_location("agent_coordination_gate", GATE_SCRIPT)
assert spec and spec.loader
gate_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = gate_module
spec.loader.exec_module(gate_module)

import agent_coordination_policy_gate as policy  # noqa: E402


def load_snapshot() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def manifest_body(
    *,
    work_item: int,
    run_id: str,
    mode: str = "independent",
    parent: int | None = None,
    paths: list[str] | None = None,
    reads: list[str] | None = None,
    contracts: list[str] | None = None,
    database: list[str] | None = None,
    migrations: list[str] | None = None,
    generated: list[str] | None = None,
    workflows: list[str] | None = None,
) -> str:
    payload = {
        "schema": gate_module.MANIFEST_SCHEMA,
        "work_item": work_item,
        "agent_run_id": run_id,
        "dependency": {"mode": mode, "stack_parent_pr": parent},
        "write_paths": paths or ["scripts/ci/**"],
        "read_paths": reads or [],
        "contracts": contracts or [],
        "database": database or [],
        "migrations": migrations or [],
        "generated_files": generated or [],
        "workflows": workflows or [],
    }
    return (
        f"Closes #{work_item}\n\n- Agent Run ID: `{run_id}`\n\n"
        f"```json\n{json.dumps(payload)}\n```"
    )


def evaluate(snapshot: dict) -> dict:
    return policy._evaluate_snapshot_policy(snapshot)


def codes(report: dict) -> set[str]:
    return set(report["reason_codes"])


def test_independent_current_prs_pass_and_historical_pr_is_ignored() -> None:
    report = evaluate(load_snapshot())
    assert report["state"] == "pass"
    assert report["counts"]["compared_current_prs"] == 1
    assert report["counts"]["ignored_historical_prs"] == 1


def test_duplicate_work_item_pr_fails_deterministically() -> None:
    snapshot = load_snapshot()
    duplicate = copy.deepcopy(snapshot["pull_request"])
    duplicate["number"] = 102
    duplicate["head_ref"] = "agent/duplicate"
    snapshot["open_pull_requests"].append(duplicate)
    report = evaluate(snapshot)
    assert "duplicate_or_missing_work_item_pr" in codes(report)


def test_broad_declared_write_path_overlap_warns() -> None:
    snapshot = load_snapshot()
    other = snapshot["open_pull_requests"][1]
    other["body"] = manifest_body(
        work_item=20,
        run_id="run-other",
        paths=["scripts/ci/**"],
    )
    other["changed_files"] = ["scripts/ci/other.py"]
    report = evaluate(snapshot)
    assert report["state"] == "warn"
    assert "broad_write_path_overlap" in codes(report)
    assert "exclusive_write_path_conflict" not in codes(report)


def test_actual_path_must_be_declared() -> None:
    snapshot = load_snapshot()
    snapshot["pull_request"]["changed_files"].append("backend/app/unsafe.py")
    snapshot["open_pull_requests"][0] = copy.deepcopy(snapshot["pull_request"])
    report = evaluate(snapshot)
    assert "actual_path_not_declared" in codes(report)


def test_blocked_issue_without_explicit_stack_fails() -> None:
    snapshot = load_snapshot()
    body = snapshot["work_item"]["body"].replace(
        "Blocked by: none", "Blocked by: #20"
    )
    snapshot["work_item"]["body"] = body
    snapshot["open_work_items"][0]["body"] = body
    snapshot["blocker_states"] = {"20": "open"}
    report = evaluate(snapshot)
    assert "unmet_blockers" in codes(report)


def test_valid_explicit_stack_on_blocking_pr_passes() -> None:
    snapshot = load_snapshot()
    body = snapshot["work_item"]["body"].replace(
        "Blocked by: none", "Blocked by: #20"
    )
    snapshot["work_item"]["body"] = body
    snapshot["open_work_items"][0]["body"] = body
    snapshot["blocker_states"] = {"20": "open"}
    snapshot["pull_request"]["body"] = manifest_body(
        work_item=10,
        run_id="run-target",
        mode="stacked",
        parent=202,
    )
    snapshot["pull_request"]["base_ref"] = "agent/parent"
    snapshot["open_pull_requests"][0] = copy.deepcopy(snapshot["pull_request"])
    report = evaluate(snapshot)
    assert report["state"] == "pass"


def test_expired_claim_requires_reclaim() -> None:
    snapshot = load_snapshot()
    snapshot["now"] = "2026-07-10T15:00:00Z"
    report = evaluate(snapshot)
    assert "active_claim_missing_or_expired" in codes(report)


def test_valid_reclaim_after_expiry_restores_authority() -> None:
    snapshot = load_snapshot()
    snapshot["work_item"]["comments"] = [
        {
            "body": "## AGENT_CLAIM\n- Run ID: `old-run`",
            "created_at": "2026-07-10T10:00:00Z",
        },
        {
            "body": "## AGENT_RECLAIM\n- New Run ID: `new-run`",
            "created_at": "2026-07-10T12:01:00Z",
        },
    ]
    snapshot["pull_request"]["body"] = manifest_body(
        work_item=10, run_id="new-run"
    )
    snapshot["pull_request"]["created_at"] = "2026-07-10T12:05:00Z"
    snapshot["open_pull_requests"][0] = copy.deepcopy(snapshot["pull_request"])
    report = evaluate(snapshot)
    assert report["state"] == "pass"


def test_overlapping_claim_does_not_steal_earlier_lease() -> None:
    snapshot = load_snapshot()
    snapshot["work_item"]["comments"].insert(
        1,
        {
            "body": "## AGENT_CLAIM\n- Run ID: `losing-run`",
            "created_at": "2026-07-10T12:05:00Z",
        },
    )
    report = evaluate(snapshot)
    assert "overlapping_active_claim" in codes(report)
    assert "agent_run_not_active_claim" not in codes(report)


def test_migration_down_revision_conflict_fails() -> None:
    snapshot = load_snapshot()
    snapshot["pull_request"]["body"] = manifest_body(
        work_item=10,
        run_id="run-target",
        paths=["backend/alembic/versions/**"],
        migrations=["down:rev-100"],
    )
    snapshot["pull_request"]["changed_files"] = [
        "backend/alembic/versions/rev_101.py"
    ]
    snapshot["open_pull_requests"][0] = copy.deepcopy(snapshot["pull_request"])
    other = snapshot["open_pull_requests"][1]
    other["body"] = manifest_body(
        work_item=20,
        run_id="run-other",
        paths=["backend/alembic/versions/**"],
        migrations=["down:rev-100"],
    )
    other["changed_files"] = ["backend/alembic/versions/rev_102.py"]
    report = evaluate(snapshot)
    assert "migration_down_revision_conflict" in codes(report)


def test_generated_workflow_conflict_fails() -> None:
    snapshot = load_snapshot()
    snapshot["pull_request"]["body"] = manifest_body(
        work_item=10,
        run_id="run-target",
        paths=[".github/workflows/shared.yml"],
        generated=[".github/workflows/shared.yml"],
        workflows=[".github/workflows/shared.yml"],
    )
    snapshot["pull_request"]["changed_files"] = [
        ".github/workflows/shared.yml"
    ]
    snapshot["open_pull_requests"][0] = copy.deepcopy(snapshot["pull_request"])
    other = snapshot["open_pull_requests"][1]
    other["body"] = manifest_body(
        work_item=20,
        run_id="run-other",
        paths=[".github/workflows/shared.yml"],
        generated=[".github/workflows/shared.yml"],
        workflows=[".github/workflows/shared.yml"],
    )
    other["changed_files"] = [".github/workflows/shared.yml"]
    report = evaluate(snapshot)
    assert {
        "exclusive_write_path_conflict",
        "generated_file_conflict",
        "workflow_conflict",
    } <= codes(report)


def test_report_is_bounded_and_redacts_sensitive_values() -> None:
    builder = gate_module.ReportBuilder(1, 2)
    for index in range(200):
        builder.add(
            "error",
            "unsafe",
            "person@example.com",
            "+41 79 123 45 67",
            "ghp_1234567890abcdef",
            f"detail-{index}-" + "x" * 500,
        )
    encoded = gate_module.bounded_report_bytes(builder.build(), 4096)
    assert len(encoded) <= 4096
    text = encoded.decode("utf-8")
    assert "person@example.com" not in text
    assert "+41 79 123 45 67" not in text
    assert "ghp_1234567890abcdef" not in text


def test_cli_fixture_produces_machine_readable_report(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    summary = tmp_path / "summary.md"
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--snapshot",
            str(FIXTURE),
            "--output",
            str(output),
            "--summary-path",
            str(summary),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["state"] == "pass"
    assert report["bounded"] is True and report["redacted"] is True
    assert "Agent Coordination Gate" in summary.read_text(encoding="utf-8")


def test_workflows_separate_trusted_enforcement_from_proposed_self_test() -> None:
    trusted = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    self_test = yaml.safe_load(SELF_TEST_WORKFLOW.read_text(encoding="utf-8"))

    assert trusted["permissions"] == {
        "contents": "read",
        "pull-requests": "read",
        "issues": "read",
    }
    assert "github.event.pull_request.number" in trusted["concurrency"]["group"]
    assert trusted["concurrency"]["cancel-in-progress"] is True
    assert trusted["jobs"]["trusted-coordination-preflight"]["timeout-minutes"] <= 10

    trusted_text = WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request_target" in trusted_text
    assert "ref: ${{ github.event.pull_request.base.sha }}" in trusted_text
    assert "working-directory: trusted" in trusted_text
    assert "Checkout proposed head" not in trusted_text
    assert "agent_coordination_policy_gate.py" in trusted_text

    assert self_test["permissions"] == {"contents": "read"}
    self_test_text = SELF_TEST_WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request_target" not in self_test_text
    assert "GITHUB_TOKEN" not in self_test_text
    assert "Evaluate live pull request" not in self_test_text
    assert "agent_coordination_policy_gate.py" in self_test_text
