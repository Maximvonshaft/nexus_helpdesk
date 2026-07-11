#!/usr/bin/env python3
"""Final fail-closed validation for hydrated Current PR authority."""
from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

import agent_coordination_path_policy as path_policy

final_policy = path_policy.final_policy
_BASE_PATH_INSTALL = path_policy.install_path_policy
_BASE_HYDRATE_CURRENT_PR_FILES = path_policy._hydrate_current_pr_files
_BASE_PATH_EVALUATE = path_policy._evaluate_snapshot_policy


def _hydrate_current_pr_files(
    snapshot: Mapping[str, Any],
    pr_loader: Callable[..., Mapping[str, Any]],
) -> dict[str, Any]:
    """Hydrate Current PR files and reject incomplete/non-open authorities."""

    adjusted = _BASE_HYDRATE_CURRENT_PR_FILES(snapshot, pr_loader)
    current_numbers = path_policy._current_pr_numbers(adjusted)
    by_number = {
        final_policy.model._pr_number(pr): pr
        for pr in adjusted.get("open_pull_requests") or []
        if isinstance(pr, Mapping)
    }
    for number in sorted(current_numbers):
        pr = by_number.get(number)
        if not isinstance(pr, Mapping):
            raise final_policy.model.GateInputError(
                f"current_pr_file_lookup_missing:pr:{number}"
            )
        if str(pr.get("state") or "").strip().lower() != "open":
            raise final_policy.model.GateInputError(
                f"current_pr_not_open:pr:{number}"
            )
    return adjusted


def _stack_parent_authority_findings(
    snapshot: Mapping[str, Any],
) -> list[Any]:
    """Require a stacked PR to use each blocker's declared Current PR."""

    target = snapshot.get("pull_request")
    work_item = snapshot.get("work_item")
    if not isinstance(target, Mapping) or not isinstance(work_item, Mapping):
        return []

    try:
        manifest = final_policy.policy.parse_manifest(target)
    except final_policy.model.GateInputError:
        return []
    if manifest.dependency_mode != "stacked" or manifest.stack_parent_pr is None:
        return []

    blockers = final_policy.model._blockers(
        final_policy.model._issue_control(str(work_item.get("body") or ""))
    )
    if not blockers:
        return []

    issue_by_number: dict[int, Mapping[str, Any]] = {}
    for issue in snapshot.get("open_work_items") or []:
        if not isinstance(issue, Mapping):
            continue
        try:
            number = final_policy.model._issue_number(issue)
        except final_policy.model.GateInputError:
            continue
        issue_by_number[number] = issue

    target_number = final_policy.model._pr_number(target)
    findings: list[Any] = []
    for blocker in blockers:
        blocker_issue = issue_by_number.get(blocker)
        if not isinstance(blocker_issue, Mapping):
            continue
        current_pr = final_policy.model._current_pr_number(
            final_policy.model._issue_control(
                str(blocker_issue.get("body") or "")
            )
        )
        if current_pr is None or current_pr == manifest.stack_parent_pr:
            continue
        findings.append(
            final_policy.model.Finding(
                "error",
                "stack_parent_not_blocker_current_pr",
                f"pr:{target_number}",
                (
                    f"blocker:{blocker}",
                    f"stack_parent:{manifest.stack_parent_pr}",
                    f"current_pr:{current_pr}",
                ),
            )
        )
    return findings


def _evaluate_snapshot_policy(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    report = _BASE_PATH_EVALUATE(snapshot)
    findings: Sequence[Any] = _stack_parent_authority_findings(snapshot)
    return final_policy.policy._append_warnings(report, findings)


def install_open_pr_policy() -> None:
    """Install the final Current-PR and stack-parent authority policy."""

    _BASE_PATH_INSTALL()
    path_policy._hydrate_current_pr_files = _hydrate_current_pr_files
    path_policy._evaluate_snapshot_policy = _evaluate_snapshot_policy
    final_policy._evaluate_snapshot_policy = _evaluate_snapshot_policy
    final_policy.policy._evaluate_snapshot_policy = _evaluate_snapshot_policy
    final_policy.policy.gate.evaluate_snapshot = _evaluate_snapshot_policy


# Every lower-level reinstall must restore the complete final policy.
path_policy.install_path_policy = install_open_pr_policy
final_policy.install_final_policy = install_open_pr_policy
final_policy.policy.install_runtime_policy = install_open_pr_policy
install_open_pr_policy()


if __name__ == "__main__":
    raise SystemExit("agent_coordination_open_pr_policy.py is import-only")
