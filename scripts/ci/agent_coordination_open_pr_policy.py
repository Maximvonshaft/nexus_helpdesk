#!/usr/bin/env python3
"""Final fail-closed validation for hydrated Current PR authority."""
from __future__ import annotations

from typing import Any, Callable, Mapping

import agent_coordination_path_policy as path_policy

_BASE_HYDRATE_CURRENT_PR_FILES = path_policy._hydrate_current_pr_files


def _hydrate_current_pr_files(
    snapshot: Mapping[str, Any],
    pr_loader: Callable[..., Mapping[str, Any]],
) -> dict[str, Any]:
    """Hydrate Current PR files and reject explicitly non-open authorities."""

    adjusted = _BASE_HYDRATE_CURRENT_PR_FILES(snapshot, pr_loader)
    current_numbers = path_policy._current_pr_numbers(adjusted)
    by_number = {
        path_policy.final_policy.model._pr_number(pr): pr
        for pr in adjusted.get("open_pull_requests") or []
        if isinstance(pr, Mapping)
    }
    for number in sorted(current_numbers):
        pr = by_number.get(number)
        if not isinstance(pr, Mapping):
            raise path_policy.final_policy.model.GateInputError(
                f"current_pr_file_lookup_missing:pr:{number}"
            )
        state = str(pr.get("state") or "").strip().lower()
        if state and state != "open":
            raise path_policy.final_policy.model.GateInputError(
                f"current_pr_not_open:pr:{number}"
            )
    return adjusted


def install_open_pr_policy() -> None:
    """Install the non-open Current PR guard into the final snapshot path."""

    path_policy._hydrate_current_pr_files = _hydrate_current_pr_files


install_open_pr_policy()


if __name__ == "__main__":
    raise SystemExit("agent_coordination_open_pr_policy.py is import-only")
