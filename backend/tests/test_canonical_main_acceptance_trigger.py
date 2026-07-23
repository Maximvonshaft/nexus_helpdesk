from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "canonical-acceptance.yml"


def _trigger_contract(workflow: str) -> str:
    lines = workflow.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("on:"):
            block = [line]
            for candidate in lines[index + 1 :]:
                if candidate.strip() and not candidate[:1].isspace():
                    break
                block.append(candidate)
            return " ".join(part.strip() for part in block if part.strip())
    raise AssertionError("canonical workflow trigger is missing")


def _assert_main_branch_trigger(contract: str, event_name: str) -> None:
    pattern = rf"{re.escape(event_name)}:\s*(?:\{{[^}}]*branches:\s*\[main\][^}}]*\}}|branches:\s*\[main\])"
    assert re.search(pattern, contract), contract


def test_canonical_acceptance_covers_pull_request_main_push_and_manual_runs() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    trigger = _trigger_contract(workflow)

    _assert_main_branch_trigger(trigger, "pull_request")
    _assert_main_branch_trigger(trigger, "push")
    assert "workflow_dispatch:" in trigger


def test_main_push_uses_the_exact_merge_commit_as_candidate_identity() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    expected_head = "${{ github.event.pull_request.head.sha || github.sha }}"
    expected_base = "${{ github.event.pull_request.base.sha || github.sha }}"
    assert f"ref: {expected_head}" in workflow
    assert f"EXPECTED_HEAD: {expected_head}" in workflow
    assert f"BASE_SHA: {expected_base}" in workflow
    assert 'if [[ "$EVENT_NAME" = "pull_request" ]]; then' in workflow
