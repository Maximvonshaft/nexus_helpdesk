#!/usr/bin/env python3
"""Segment-aware path, input, and review-transition policy for Nexus OSR.

The repository manifests use POSIX-style paths. A single-segment wildcard must
never consume ``/``; ``**`` is the only recursive directory wildcard, and a
trailing slash denotes the complete directory subtree. Documented ``?`` and
character-class globs are accepted as bounded resource identifiers. GitHub
renames contribute both destination and previous paths to changed-file
coordination. Leading-dot path segments are preserved. Blocker hydration also
hydrates every newly discovered open Current PR with its complete file list
before conflict evaluation and fails closed if the recorded Current PR is not
open. Entering Ready for Review requires a Delivery comment bound to the exact
current Head, even while an implementation Claim is active.
"""
from __future__ import annotations

import copy
import fnmatch
from functools import lru_cache
import re
from pathlib import Path
from typing import Any, Callable, Mapping

import agent_coordination_policy_gate as final_policy

_BASE_FINAL_INSTALL = final_policy.install_final_policy
_BASE_FINAL_EVALUATE = final_policy._evaluate_snapshot_policy
_BASE_HYDRATED_SNAPSHOT_FROM_EVENT = final_policy.policy._snapshot_from_event_policy
_SAFE_GLOB_TOKEN_RE = re.compile(r"^[A-Za-z0-9_./*?#:+\[\]\-]+$")


def _normalize_path_spec(value: str) -> str:
    """Normalize one optional ``./`` prefix without removing leading dots."""

    normalized = str(value or "").strip()
    return normalized[2:] if normalized.startswith("./") else normalized


def _segments(value: str, *, directory_scope: bool) -> tuple[str, ...]:
    normalized = _normalize_path_spec(value)
    if directory_scope and normalized.endswith("/"):
        normalized += "**"
    return tuple(segment for segment in normalized.split("/") if segment)


def _path_matches(path: str, spec: str) -> bool:
    """Match a concrete repository path without allowing ``*`` across ``/``."""

    path_segments = _segments(path, directory_scope=False)
    spec_segments = _segments(spec, directory_scope=True)

    @lru_cache(maxsize=None)
    def matches(path_index: int, spec_index: int) -> bool:
        if spec_index == len(spec_segments):
            return path_index == len(path_segments)
        token = spec_segments[spec_index]
        if token == "**":
            return matches(path_index, spec_index + 1) or (
                path_index < len(path_segments)
                and matches(path_index + 1, spec_index)
            )
        if path_index == len(path_segments):
            return False
        return fnmatch.fnmatchcase(path_segments[path_index], token) and matches(
            path_index + 1,
            spec_index + 1,
        )

    return matches(0, 0)


def _path_specs_overlap(left: str, right: str) -> bool:
    """Return whether two POSIX path specifications can match one path."""

    left_segments = _segments(left, directory_scope=True)
    right_segments = _segments(right, directory_scope=True)

    @lru_cache(maxsize=None)
    def overlaps(left_index: int, right_index: int) -> bool:
        if left_index == len(left_segments) and right_index == len(right_segments):
            return True
        if left_index == len(left_segments):
            return all(token == "**" for token in right_segments[right_index:])
        if right_index == len(right_segments):
            return all(token == "**" for token in left_segments[left_index:])

        left_token = left_segments[left_index]
        right_token = right_segments[right_index]
        if left_token == "**" and right_token == "**":
            return overlaps(left_index + 1, right_index) or overlaps(
                left_index,
                right_index + 1,
            )
        if left_token == "**":
            return overlaps(left_index + 1, right_index) or overlaps(
                left_index,
                right_index + 1,
            )
        if right_token == "**":
            return overlaps(left_index, right_index + 1) or overlaps(
                left_index + 1,
                right_index,
            )
        return final_policy._segment_specs_overlap(
            left_token,
            right_token,
        ) and overlaps(left_index + 1, right_index + 1)

    return overlaps(0, 0)


def _github_pr_with_rename_paths(
    self: Any,
    number: int,
    *,
    include_files: bool,
) -> dict[str, Any]:
    """Read a PR and treat both sides of a rename as changed resources."""

    raw = final_policy.policy.gate._as_mapping(
        self.get(f"/repos/{self.repository}/pulls/{number}"),
        field_name="github_pr",
    )
    files = (
        self.get_paginated(f"/repos/{self.repository}/pulls/{number}/files")
        if include_files
        else []
    )
    changed_paths: list[str] = []
    for entry in files:
        if not isinstance(entry, Mapping):
            continue
        filename = entry.get("filename")
        if isinstance(filename, str) and filename.strip():
            changed_paths.append(filename.strip())
        previous = entry.get("previous_filename")
        if (
            str(entry.get("status") or "").lower() == "renamed"
            and isinstance(previous, str)
            and previous.strip()
        ):
            changed_paths.append(previous.strip())

    return {
        "number": int(raw["number"]),
        "state": raw.get("state"),
        "draft": bool(raw.get("draft")),
        "body": raw.get("body") or "",
        "head_sha": (raw.get("head") or {}).get("sha"),
        "head_ref": (raw.get("head") or {}).get("ref"),
        "base_ref": (raw.get("base") or {}).get("ref"),
        "created_at": raw.get("created_at"),
        "updated_at": raw.get("updated_at"),
        "changed_files": list(dict.fromkeys(changed_paths)),
    }


def _current_pr_numbers(snapshot: Mapping[str, Any]) -> set[int]:
    numbers: set[int] = set()
    for issue in snapshot.get("open_work_items") or []:
        if not isinstance(issue, Mapping):
            continue
        current = final_policy.model._current_pr_number(
            final_policy.model._issue_control(str(issue.get("body") or ""))
        )
        if current is not None:
            numbers.add(current)
    target = snapshot.get("pull_request")
    if isinstance(target, Mapping):
        numbers.add(final_policy.model._pr_number(target))
    return numbers


def _hydrate_current_pr_files(
    snapshot: Mapping[str, Any],
    pr_loader: Callable[..., Mapping[str, Any]],
) -> dict[str, Any]:
    """Refetch open Current PRs after blocker Issues have been hydrated."""

    adjusted = copy.deepcopy(dict(snapshot))
    open_prs = adjusted.get("open_pull_requests")
    if not isinstance(open_prs, list):
        raise final_policy.model.GateInputError("open_pull_requests_must_be_array")

    hydrated_by_number: dict[int, dict[str, Any]] = {}
    for number in sorted(_current_pr_numbers(adjusted)):
        try:
            hydrated = pr_loader(number, include_files=True)
        except final_policy.model.GateInputError:
            raise
        except Exception as exc:
            raise final_policy.model.GateInputError(
                f"current_pr_file_lookup_unavailable:pr:{number}"
            ) from exc
        if not isinstance(hydrated, Mapping):
            raise final_policy.model.GateInputError(
                f"current_pr_file_lookup_invalid:pr:{number}"
            )
        if str(hydrated.get("state") or "").strip().lower() != "open":
            raise final_policy.model.GateInputError(
                f"current_pr_not_open:pr:{number}"
            )
        hydrated_by_number[number] = dict(hydrated)

    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    for raw_pr in open_prs:
        if not isinstance(raw_pr, Mapping):
            continue
        number = final_policy.model._pr_number(raw_pr)
        result.append(hydrated_by_number.get(number, dict(raw_pr)))
        seen.add(number)
    for number in sorted(hydrated_by_number):
        if number not in seen:
            result.append(hydrated_by_number[number])

    adjusted["open_pull_requests"] = result
    target = adjusted.get("pull_request")
    if isinstance(target, Mapping):
        target_number = final_policy.model._pr_number(target)
        if target_number in hydrated_by_number:
            adjusted["pull_request"] = hydrated_by_number[target_number]
    return adjusted


def _snapshot_from_event_with_current_pr_files(
    self: Any,
    event_path: Path,
    now: Any,
) -> dict[str, Any]:
    snapshot = _BASE_HYDRATED_SNAPSHOT_FROM_EVENT(self, event_path, now)
    return _hydrate_current_pr_files(snapshot, self.pr)


def _ready_delivery_is_exact(snapshot: Mapping[str, Any]) -> bool:
    target = snapshot.get("pull_request")
    work_item = snapshot.get("work_item")
    if not isinstance(target, Mapping) or not isinstance(work_item, Mapping):
        return False
    try:
        manifest = final_policy.policy.parse_manifest(target)
    except final_policy.model.GateInputError:
        return False
    comments = [
        comment
        for comment in (work_item.get("comments") or [])
        if isinstance(comment, Mapping)
    ]
    current_head = str(target.get("head_sha") or "").strip().lower()
    return bool(
        current_head
        and current_head
        in final_policy._delivery_heads(comments).get(manifest.agent_run_id, set())
    )


def _evaluate_snapshot_policy(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    report = _BASE_FINAL_EVALUATE(snapshot)
    action = str(snapshot.get("event_action") or "synchronize").strip().lower()
    if action != "ready_for_review" or _ready_delivery_is_exact(snapshot):
        return report

    target = snapshot.get("pull_request")
    number = (
        final_policy.model._pr_number(target)
        if isinstance(target, Mapping)
        else 0
    )
    return final_policy.policy._append_warnings(
        report,
        [
            final_policy.model.Finding(
                "error",
                "delivery_head_required_for_review",
                f"pr:{number}",
            )
        ],
    )


def _patch_input_and_path_semantics() -> None:
    final_policy.model._SAFE_TOKEN_RE = _SAFE_GLOB_TOKEN_RE
    final_policy.model._normalize_path_spec = _normalize_path_spec
    final_policy.core._normalize_path_spec = _normalize_path_spec
    final_policy.model._path_matches = _path_matches
    final_policy.core._path_matches = _path_matches
    final_policy.model._path_specs_overlap = _path_specs_overlap
    final_policy.core._path_specs_overlap = _path_specs_overlap
    final_policy._path_specs_overlap = _path_specs_overlap
    final_policy.policy.gate.GitHubReader.pr = _github_pr_with_rename_paths
    final_policy.policy.gate.GitHubReader.snapshot_from_event = (
        _snapshot_from_event_with_current_pr_files
    )


def install_path_policy() -> None:
    """Install final temporal, input, path, and review-transition rules."""

    _BASE_FINAL_INSTALL()
    _patch_input_and_path_semantics()
    final_policy._evaluate_snapshot_policy = _evaluate_snapshot_policy
    final_policy.policy._evaluate_snapshot_policy = _evaluate_snapshot_policy
    final_policy.policy.gate.evaluate_snapshot = _evaluate_snapshot_policy


# Every lower-level compatibility reinstall must restore the complete policy.
final_policy.install_final_policy = install_path_policy
final_policy.policy.install_runtime_policy = install_path_policy
install_path_policy()


if __name__ == "__main__":
    raise SystemExit("agent_coordination_path_policy.py is an import-only policy module")
