#!/usr/bin/env python3
"""Final workflow entrypoint for Nexus OSR coordination policy.

The lower-level modules remain deterministic and fixture-friendly. This entry
installs the repository's final fail-closed policy on every invocation:

- safe existing-PR Reclaim with a required prior lease;
- Delivery authority bound to the exact delivered Head SHA;
- bounded manifest failures for malformed stack metadata;
- real file-glob intersection checks;
- broad-directory/file-glob and read/write review warnings;
- original manifest coverage retained for undeclared-path enforcement.
"""
from __future__ import annotations

import copy
from functools import lru_cache
import fnmatch
import json
import re
from typing import Any, Mapping, Sequence

import agent_coordination_reclaim_adapter as policy

_BASE_INSTALL = policy.install_runtime_policy
_BASE_PARSE_MANIFEST = policy.model.parse_manifest
_BASE_PARSE_LEASES = policy._parse_leases_policy
_WILDCARD_RE = re.compile(r"[*?[]")
_LITERAL_RUN_RE = re.compile(r"[A-Za-z0-9._-]+")
_DELIVERY_HEAD_RE = re.compile(
    r"(?mi)^-\s*(?:Exact(?:\s+review)?\s+head|Delivered\s+head|Head\s+SHA):\s*"
    r"`?(?P<value>[0-9a-f]{40,64})`?\s*$"
)


def _parse_manifest_policy(pr: Mapping[str, Any]):  # noqa: ANN201
    try:
        return _BASE_PARSE_MANIFEST(pr)
    except policy.model.GateInputError:
        raise
    except (TypeError, ValueError) as exc:
        raise policy.model.GateInputError("manifest_stack_parent_invalid") from exc


def _parse_leases_policy(comments):  # noqa: ANN001, ANN201
    ordered: list[tuple[object, Mapping[str, Any]]] = []
    for comment in comments:
        if not isinstance(comment, Mapping):
            continue
        event = policy.model._comment_event(comment)
        if event is not None:
            ordered.append((event, comment))
    ordered.sort(key=lambda item: item[0].created_at)

    prior_lease_exists = False
    invalid_comment_ids: set[int] = set()
    findings: list[policy.model.Finding] = []
    for event, comment in ordered:
        if event.kind == "AGENT_CLAIM":
            prior_lease_exists = True
            continue
        if event.kind != "AGENT_RECLAIM":
            continue
        if not prior_lease_exists:
            invalid_comment_ids.add(id(comment))
            findings.append(
                policy.model.Finding(
                    "error",
                    "reclaim_without_prior_lease",
                    f"run:{event.run_id}",
                )
            )
        else:
            prior_lease_exists = True

    filtered = [
        comment
        for comment in comments
        if not isinstance(comment, Mapping) or id(comment) not in invalid_comment_ids
    ]
    leases, base_findings = _BASE_PARSE_LEASES(filtered)
    return leases, findings + base_findings


def _has_wildcard(value: str) -> bool:
    return bool(_WILDCARD_RE.search(value))


def _fixed_prefix(pattern: str) -> str:
    match = _WILDCARD_RE.search(pattern)
    return pattern if match is None else pattern[: match.start()]


def _fixed_suffix(pattern: str) -> str:
    positions = [position for character in "*?[" if (position := pattern.rfind(character)) >= 0]
    return pattern if not positions else pattern[max(positions) + 1 :]


def _materialize(pattern: str, star_value: str = "x") -> str:
    result: list[str] = []
    index = 0
    while index < len(pattern):
        character = pattern[index]
        if character == "*":
            result.append(star_value)
            index += 1
            continue
        if character == "?":
            result.append("x")
            index += 1
            continue
        if character == "[":
            closing = pattern.find("]", index + 1)
            if closing >= 0:
                body = pattern[index + 1 : closing]
                if body.startswith(("!", "^")):
                    body = body[1:]
                result.append(body[0] if body else "x")
                index = closing + 1
                continue
        result.append(character)
        index += 1
    return "".join(result)


def _segment_specs_overlap(left: str, right: str) -> bool:
    left_wild = _has_wildcard(left)
    right_wild = _has_wildcard(right)
    if not left_wild and not right_wild:
        return left == right
    if not left_wild:
        return fnmatch.fnmatchcase(left, right)
    if not right_wild:
        return fnmatch.fnmatchcase(right, left)

    left_prefix = _fixed_prefix(left)
    right_prefix = _fixed_prefix(right)
    if left_prefix and right_prefix and not (
        left_prefix.startswith(right_prefix) or right_prefix.startswith(left_prefix)
    ):
        return False
    left_suffix = _fixed_suffix(left)
    right_suffix = _fixed_suffix(right)
    if left_suffix and right_suffix and not (
        left_suffix.endswith(right_suffix) or right_suffix.endswith(left_suffix)
    ):
        return False

    literal_values = [
        value
        for value in _LITERAL_RUN_RE.findall(left) + _LITERAL_RUN_RE.findall(right)
        if value
    ]
    candidates = {
        _materialize(left),
        _materialize(right),
    }
    for value in literal_values[:12]:
        candidates.add(_materialize(left, value))
        candidates.add(_materialize(right, value))
    for candidate in candidates:
        if fnmatch.fnmatchcase(candidate, left) and fnmatch.fnmatchcase(candidate, right):
            return True

    # Both are bounded single-segment globs and no fixed prefix/suffix proved
    # them disjoint. Conservatively retain possible intersection.
    return True


def _path_specs_overlap(left: str, right: str) -> bool:
    left_normalized = policy.model._normalize_path_spec(left)
    right_normalized = policy.model._normalize_path_spec(right)
    left_segments = tuple(segment for segment in left_normalized.split("/") if segment)
    right_segments = tuple(segment for segment in right_normalized.split("/") if segment)

    @lru_cache(maxsize=None)
    def overlaps(left_index: int, right_index: int) -> bool:
        if left_index == len(left_segments) and right_index == len(right_segments):
            return True
        if left_index == len(left_segments):
            return all(segment == "**" for segment in right_segments[right_index:])
        if right_index == len(right_segments):
            return all(segment == "**" for segment in left_segments[left_index:])

        left_segment = left_segments[left_index]
        right_segment = right_segments[right_index]
        if left_segment == "**" and right_segment == "**":
            return overlaps(left_index + 1, right_index) or overlaps(
                left_index, right_index + 1
            )
        if left_segment == "**":
            return overlaps(left_index + 1, right_index) or overlaps(
                left_index, right_index + 1
            )
        if right_segment == "**":
            return overlaps(left_index, right_index + 1) or overlaps(
                left_index + 1, right_index
            )
        return _segment_specs_overlap(left_segment, right_segment) and overlaps(
            left_index + 1, right_index + 1
        )

    return overlaps(0, 0)


def _is_broad_path(spec: str) -> bool:
    normalized = policy.model._normalize_path_spec(spec)
    return normalized.endswith("/") or _has_wildcard(normalized)


def _rewrite_manifest_for_core(pr: Mapping[str, Any]) -> dict[str, Any]:
    adjusted = copy.deepcopy(dict(pr))
    body = str(adjusted.get("body") or "")
    payload, match = policy._manifest_payload(body)
    if payload is None or match is None:
        return adjusted

    writes = policy.model._manifest_tokens(payload, "write_paths")
    exact_writes = [spec for spec in writes if not _is_broad_path(spec)]
    actual = list(policy.model._changed_files(adjusted))
    covered_actual = [
        path
        for path in actual
        if any(policy.model._path_matches(path, spec) for spec in writes)
    ]
    narrowed = list(dict.fromkeys(exact_writes + covered_actual))
    if not narrowed:
        narrowed = list(writes)

    payload["write_paths"] = narrowed
    replacement = "```json\n" + json.dumps(payload, sort_keys=True) + "\n```"
    adjusted["body"] = body[: match.start()] + replacement + body[match.end() :]
    return adjusted


def _delivery_heads(comments: Sequence[Mapping[str, Any]]) -> dict[str, set[str]]:
    delivered: dict[str, set[str]] = {}
    for comment in comments:
        event = policy.model._comment_event(comment)
        if event is None or event.kind != "AGENT_DELIVERY":
            continue
        match = _DELIVERY_HEAD_RE.search(str(comment.get("body") or ""))
        if match is None:
            continue
        delivered.setdefault(event.run_id, set()).add(match.group("value").lower())
    return delivered


def _specific_glob_conflict_findings(
    snapshot: Mapping[str, Any],
) -> list[policy.model.Finding]:
    target = snapshot.get("pull_request")
    if not isinstance(target, Mapping):
        return []
    target_number = policy.model._pr_number(target)
    target_writes, _ = policy._access_paths(target)
    target_actual = set(policy.model._changed_files(target))
    current_numbers = policy._current_pr_numbers(snapshot)
    findings: list[policy.model.Finding] = []

    for other in snapshot.get("open_pull_requests") or []:
        if not isinstance(other, Mapping):
            continue
        other_number = policy.model._pr_number(other)
        if other_number == target_number or other_number not in current_numbers:
            continue
        other_writes, _ = policy._access_paths(other)
        other_actual = set(policy.model._changed_files(other))
        if target_actual & other_actual:
            continue

        pairs: list[str] = []
        for left in target_writes:
            left_normalized = policy.model._normalize_path_spec(left)
            left_basename = left_normalized.rsplit("/", 1)[-1]
            for right in other_writes:
                right_normalized = policy.model._normalize_path_spec(right)
                right_basename = right_normalized.rsplit("/", 1)[-1]
                if left_normalized == right_normalized:
                    continue
                if not (_has_wildcard(left_normalized) and _has_wildcard(right_normalized)):
                    continue
                if left_normalized.endswith("/**") or right_normalized.endswith("/**"):
                    continue
                if not _path_specs_overlap(left_normalized, right_normalized):
                    continue
                # A differing intersecting glob pair is blocking only when at
                # least one side fixes the concrete basename. Identical or
                # catch-all filename globs remain broad review warnings.
                if _has_wildcard(left_basename) and _has_wildcard(right_basename):
                    continue
                pairs.append(f"{left}<->{right}")
        if pairs:
            findings.append(
                policy.model.Finding(
                    "error",
                    "exclusive_write_path_conflict",
                    f"pr:{target_number}",
                    (f"other:pr:{other_number}", *tuple(pairs[:5])),
                )
            )
    return findings


def _evaluate_snapshot_policy(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    policy.install_runtime_policy()
    original_snapshot = copy.deepcopy(dict(snapshot))
    adjusted = policy._rewrite_open_blockers(original_snapshot)
    target = adjusted.get("pull_request")
    work_item = adjusted.get("work_item")
    if not isinstance(target, Mapping) or not isinstance(work_item, Mapping):
        raise policy.model.GateInputError("coordination_snapshot_target_invalid")

    manifest = policy.parse_manifest(target)
    comments = [
        comment
        for comment in (work_item.get("comments") or [])
        if isinstance(comment, Mapping)
    ]
    delivered_heads = _delivery_heads(comments)
    current_head = str(target.get("head_sha") or "").strip().lower()
    action = str(adjusted.get("event_action") or "synchronize").lower()

    policy._EVALUATION_RUN_ID = manifest.agent_run_id
    policy._EVALUATION_ALLOW_DELIVERED = bool(
        current_head
        and action in policy._NON_WRITING_ACTIONS
        and current_head in delivered_heads.get(manifest.agent_run_id, set())
    )
    try:
        core_snapshot = policy._prepare_access_snapshot(adjusted)
        report = policy._ORIGINAL_EVALUATE_SNAPSHOT(core_snapshot)
        access_findings = list(policy._warning_findings(original_snapshot))
        access_findings.extend(_specific_glob_conflict_findings(original_snapshot))
        return policy._append_warnings(report, access_findings)
    finally:
        policy._EVALUATION_RUN_ID = None
        policy._EVALUATION_ALLOW_DELIVERED = False


def install_final_policy() -> None:
    _BASE_INSTALL()
    policy.model.parse_manifest = _parse_manifest_policy
    policy.core.parse_manifest = _parse_manifest_policy
    policy.gate.parse_manifest = _parse_manifest_policy
    policy.parse_manifest = _parse_manifest_policy
    policy.model.parse_leases = _parse_leases_policy
    policy.core.parse_leases = _parse_leases_policy
    policy.model._path_specs_overlap = _path_specs_overlap
    policy.core._path_specs_overlap = _path_specs_overlap
    policy._is_broad_path = _is_broad_path
    policy._rewrite_manifest_for_core = _rewrite_manifest_for_core
    policy._evaluate_snapshot_policy = _evaluate_snapshot_policy
    policy.gate.evaluate_snapshot = _evaluate_snapshot_policy


# Ensure any lower-level call that reinstalls base compatibility also restores
# the final rules before evaluation continues.
policy.install_runtime_policy = install_final_policy
install_final_policy()

# Re-export the tested policy surface used by focused fixtures.
apply_reclaim_implementation_start = policy.apply_reclaim_implementation_start
hydrate_blocker_authority = policy.hydrate_blocker_authority
_canonical_resource_intersection = policy._canonical_resource_intersection
core = policy.core
model = policy.model


def main(argv: Sequence[str] | None = None) -> int:
    install_final_policy()
    return policy.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
