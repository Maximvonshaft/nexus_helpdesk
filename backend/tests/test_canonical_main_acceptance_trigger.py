from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "canonical-acceptance.yml"


def test_canonical_acceptance_covers_pull_request_main_push_and_manual_runs() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    trigger = re.search(r"(?m)^on:\s*\{[^\n]+\}\s*$", workflow)

    assert trigger is not None
    trigger_contract = trigger.group(0)
    assert "pull_request: {branches: [main]}" in trigger_contract
    assert "push: {branches: [main]}" in trigger_contract
    assert "workflow_dispatch: {}" in trigger_contract


def test_main_push_uses_the_exact_merge_commit_as_candidate_identity() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    expected_head = "${{ github.event.pull_request.head.sha || github.sha }}"
    expected_base = "${{ github.event.pull_request.base.sha || github.sha }}"
    assert f"ref: {expected_head}" in workflow
    assert f"EXPECTED_HEAD: {expected_head}" in workflow
    assert f"BASE_SHA: {expected_base}" in workflow
    assert 'if [[ "$EVENT_NAME" = "pull_request" ]]; then' in workflow
