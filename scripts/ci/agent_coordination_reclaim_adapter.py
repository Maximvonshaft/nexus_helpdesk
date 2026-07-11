#!/usr/bin/env python3
"""Install current coordination policy before the pure gate evaluation.

The underlying parser/evaluator remains usable for deterministic fixtures. This
workflow entrypoint adds the repository's current governance compatibility and
server-evidence rules:

- accepted Claim/Reclaim/Delivery metadata;
- release-aware historical authorization and safe existing-PR reclaim;
- direct lookup of every declared blocker regardless of label;
- exact write conflicts versus broad/read dependency warnings;
- resource identity comparison before presentation-layer redaction.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Iterable, Mapping, Sequence

import agent_coordination_core as core
import agent_coordination_gate as gate
import agent_coordination_model as model
from agent_coordination_model import GateInputError, Finding, LeaseState, _parse_time, parse_manifest

_ORIGINAL_LOAD_SNAPSHOT = gate.load_snapshot
_ORIGINAL_SNAPSHOT_FROM_EVENT = gate.GitHubReader.snapshot_from_event
_ORIGINAL_EVALUATE_SNAPSHOT = gate.evaluate_snapshot

_ACCEPTED_RUN_ID_RE = re.compile(
    r"(?mi)^-\s*(?:Agent Run ID|Run ID|New Run ID|New Agent Run ID):\s*`?(?P<value>[^`\n]+)`?\s*$"
)
_ACCEPTED_HEADING_RE = re.compile(
    r"(?m)^##\s+(?P<heading>AGENT_CLAIM|AGENT_HEARTBEAT|AGENT_HANDOFF|AGENT_RECLAIM|AGENT_DELIVERY)\s*$"
)
_BLOCKED_BY_RE = re.compile(r"(?mi)^-\s*Blocked by:\s*.*$")
_MANIFEST_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
_IGNORED_RESOURCE_TOKENS = {"none", "n/a", "not_applicable"}
_NON_WRITING_ACTIONS = {"edited", "ready_for_review", "reopened"}

_EVALUATION_RUN_ID: str | None = None
_EVALUATION_ALLOW_DELIVERED = False


def _lease_authorized_at(self: LeaseState, instant: dt.datetime) -> bool:
    return (
        self.started_at <= instant < self.expires_at
        and (self.released_at is None or instant < self.released_at)
    )


def _canonical_resource_intersection(left: Iterable[str], right: Iterable[str]) -> set[str]:
    """Compare bounded manifest identities before output redaction."""

    def normalized(values: Iterable[str]) -> set[str]:
        result: set[str] = set()
        for value in values:
            token = model._safe_token(value)
            if token is None:
                continue
            canonical = token.lower()
            if canonical not in _IGNORED_RESOURCE_TOKENS:
                result.add(canonical)
        return result

    return normalized(left) & normalized(right)


def _parse_leases_policy(
    comments: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, LeaseState], list[Finding]]:
    events = sorted(
        (event for comment in comments if (event := model._comment_event(comment))),
        key=lambda item: item.created_at,
    )
    leases: dict[str, LeaseState] = {}
    findings: list[Finding] = []
    active_run: str | None = None

    for event in events:
        if event.kind in {"AGENT_CLAIM", "AGENT_RECLAIM"}:
            if active_run:
                prior = leases[active_run]
                prior_released = (
                    prior.released_at is not None
                    and prior.released_at <= event.created_at
                )
                prior_expired = prior.expires_at <= event.created_at
                if event.kind == "AGENT_CLAIM" and not (prior_released or prior_expired):
                    findings.append(
                        Finding(
                            "error",
                            "overlapping_active_claim",
                            f"run:{event.run_id}",
                            (f"winning_run:{active_run}",),
                        )
                    )
                    continue
                if event.kind == "AGENT_RECLAIM" and not (prior_released or prior_expired):
                    findings.append(
                        Finding(
                            "error",
                            "reclaim_before_release_or_expiry",
                            f"run:{event.run_id}",
                            (f"prior:{active_run}",),
                        )
                    )
                    continue
            active_run = event.run_id
            leases[event.run_id] = LeaseState(
                run_id=event.run_id,
                started_at=event.created_at,
                expires_at=event.created_at
                + dt.timedelta(minutes=model.DEFAULT_LEASE_MINUTES),
                released_at=None,
            )
            continue

        if event.run_id not in leases:
            findings.append(
                Finding(
                    "error",
                    "lease_event_without_claim",
                    f"run:{event.run_id}",
                    (event.kind.lower(),),
                )
            )
            continue

        current = leases[event.run_id]
        if event.kind == "AGENT_HEARTBEAT":
            if current.released_at is not None:
                findings.append(
                    Finding("error", "heartbeat_after_release", f"run:{event.run_id}")
                )
            elif event.created_at > current.expires_at:
                findings.append(
                    Finding(
                        "error",
                        "heartbeat_after_lease_expiry",
                        f"run:{event.run_id}",
                    )
                )
            else:
                leases[event.run_id] = LeaseState(
                    run_id=current.run_id,
                    started_at=current.started_at,
                    expires_at=event.created_at
                    + dt.timedelta(minutes=model.DEFAULT_LEASE_MINUTES),
                    released_at=None,
                )
            continue

        release_event = event.kind == "AGENT_DELIVERY" or (
            event.kind == "AGENT_HANDOFF" and event.released
        )
        if release_event:
            if event.created_at > current.expires_at:
                findings.append(
                    Finding(
                        "error",
                        "delivery_or_handoff_after_lease_expiry",
                        f"run:{event.run_id}",
                    )
                )
                continue
            leases[event.run_id] = LeaseState(
                run_id=current.run_id,
                started_at=current.started_at,
                expires_at=current.expires_at,
                released_at=event.created_at,
            )
            if active_run == event.run_id:
                active_run = None

    return leases, findings


def _active_lease_policy(
    leases: Mapping[str, LeaseState], now: dt.datetime
) -> LeaseState | None:
    active = [lease for lease in leases.values() if lease.active_at(now)]
    if active:
        return max(active, key=lambda lease: lease.started_at)
    if _EVALUATION_ALLOW_DELIVERED and _EVALUATION_RUN_ID:
        return leases.get(_EVALUATION_RUN_ID)
    return None


def _event_pr_updated_at() -> str | None:
    event_path = os.environ.get("GITHUB_EVENT_PATH", "").strip()
    if not event_path:
        return None
    try:
        payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    pull_request = payload.get("pull_request")
    if not isinstance(pull_request, Mapping):
        return None
    value = pull_request.get("updated_at")
    return str(value) if isinstance(value, str) and value.strip() else None


def _matching_reclaim_time(
    comments: Sequence[Mapping[str, Any]], run_id: str
) -> dt.datetime | None:
    matches: list[dt.datetime] = []
    for comment in comments:
        event = model._comment_event(comment)
        if event is None or event.kind != "AGENT_RECLAIM" or event.run_id != run_id:
            continue
        matches.append(event.created_at)
    return max(matches) if matches else None


def _delivered_run_ids(comments: Sequence[Mapping[str, Any]]) -> set[str]:
    delivered: set[str] = set()
    for comment in comments:
        event = model._comment_event(comment)
        if event is not None and event.kind == "AGENT_DELIVERY":
            delivered.add(event.run_id)
    return delivered


def apply_reclaim_implementation_start(
    snapshot: Mapping[str, Any],
    *,
    event_updated_at: str | None = None,
) -> dict[str, Any]:
    """Use a server-timestamped Reclaim as an existing PR's effective start."""

    install_runtime_policy()
    adjusted = copy.deepcopy(dict(snapshot))
    pull_request = adjusted.get("pull_request")
    work_item = adjusted.get("work_item")
    if not isinstance(pull_request, dict) or not isinstance(work_item, Mapping):
        raise GateInputError("reclaim_adapter_snapshot_invalid")

    manifest = parse_manifest(pull_request)
    comments = work_item.get("comments") or []
    if not isinstance(comments, list):
        raise GateInputError("work_item_comments_must_be_array")
    reclaim_time = _matching_reclaim_time(
        [comment for comment in comments if isinstance(comment, Mapping)],
        manifest.agent_run_id,
    )
    if reclaim_time is None:
        return adjusted

    original_start = _parse_time(
        pull_request.get("created_at"), field_name="pr_created_at"
    )
    if reclaim_time <= original_start:
        return adjusted

    updated_raw = pull_request.get("updated_at") or event_updated_at
    updated_at = _parse_time(updated_raw, field_name="pr_updated_at")
    if updated_at < reclaim_time:
        raise GateInputError("reclaim_not_reflected_in_pr_update")

    effective = reclaim_time.isoformat().replace("+00:00", "Z")
    pull_request["created_at"] = effective
    pull_request["implementation_started_at"] = effective
    pull_request["implementation_start_authority"] = "agent_reclaim_comment"
    return adjusted


def _issue_without_comments(issue: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "number": int(issue["number"]),
        "state": issue.get("state"),
        "body": issue.get("body") or "",
        "labels": issue.get("labels") or [],
        "comments": [],
    }


def hydrate_blocker_authority(
    snapshot: Mapping[str, Any],
    issue_loader: Callable[[int], Mapping[str, Any]],
) -> dict[str, Any]:
    """Resolve every referenced blocker directly, regardless of label."""

    adjusted = copy.deepcopy(dict(snapshot))
    work_item = adjusted.get("work_item")
    if not isinstance(work_item, Mapping):
        raise GateInputError("work_item_required_for_blocker_lookup")
    declared = model._blockers(model._issue_control(str(work_item.get("body") or "")))
    states: dict[str, str] = {}
    open_issues = adjusted.setdefault("open_work_items", [])
    if not isinstance(open_issues, list):
        raise GateInputError("open_work_items_must_be_array")
    known = {
        int(issue["number"])
        for issue in open_issues
        if isinstance(issue, Mapping) and str(issue.get("number") or "").isdigit()
    }

    for number in declared:
        try:
            issue = issue_loader(number)
        except GateInputError:
            raise
        except Exception as exc:  # fail closed without arbitrary API details
            raise GateInputError(f"blocker_lookup_unavailable:issue:{number}") from exc
        state = str(issue.get("state") or "unknown").lower()
        states[str(number)] = state
        if state == "open" and number not in known:
            open_issues.append(_issue_without_comments(issue))
            known.add(number)

    adjusted["blocker_states"] = states
    return adjusted


def _rewrite_open_blockers(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    adjusted = copy.deepcopy(dict(snapshot))
    states = adjusted.get("blocker_states")
    work_item = adjusted.get("work_item")
    if not isinstance(states, Mapping) or not isinstance(work_item, dict):
        return adjusted
    declared = model._blockers(model._issue_control(str(work_item.get("body") or "")))
    missing = [number for number in declared if str(number) not in states]
    if missing:
        raise GateInputError("blocker_lookup_missing")
    open_numbers = [number for number in declared if states.get(str(number)) == "open"]
    replacement = "- Blocked by: " + (
        ", ".join(f"#{number}" for number in open_numbers) if open_numbers else "none"
    )
    body = str(work_item.get("body") or "")
    if _BLOCKED_BY_RE.search(body):
        body = _BLOCKED_BY_RE.sub(replacement, body, count=1)
    else:
        body = replacement + "\n" + body
    work_item["body"] = body
    return adjusted


def _manifest_payload(body: str) -> tuple[dict[str, Any] | None, re.Match[str] | None]:
    for match in _MANIFEST_BLOCK_RE.finditer(body or ""):
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("schema") == model.MANIFEST_SCHEMA:
            return payload, match
    return None, None


def _is_broad_path(spec: str) -> bool:
    normalized = model._normalize_path_spec(spec)
    return normalized.endswith("/") or normalized.endswith("/**") or any(
        character in normalized for character in "*?["
    )


def _access_paths(pr: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    body = str(pr.get("body") or "")
    payload, _ = _manifest_payload(body)
    if payload is not None:
        writes = model._manifest_tokens(payload, "write_paths")
        reads = model._manifest_tokens(payload, "read_paths")
        return writes, reads
    return (
        model._fallback_resource_tokens(body, "Declared write paths"),
        model._fallback_resource_tokens(body, "Declared read paths"),
    )


def _rewrite_manifest_for_core(pr: Mapping[str, Any]) -> dict[str, Any]:
    adjusted = copy.deepcopy(dict(pr))
    body = str(adjusted.get("body") or "")
    payload, match = _manifest_payload(body)
    if payload is None or match is None:
        return adjusted
    writes = model._manifest_tokens(payload, "write_paths")
    exact_writes = [spec for spec in writes if not _is_broad_path(spec)]
    actual = list(model._changed_files(adjusted))
    narrowed = list(dict.fromkeys(exact_writes + actual))
    if not narrowed:
        narrowed = list(writes)
    payload["write_paths"] = narrowed
    replacement = "```json\n" + json.dumps(payload, sort_keys=True) + "\n```"
    adjusted["body"] = body[: match.start()] + replacement + body[match.end() :]
    return adjusted


def _prepare_access_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    adjusted = copy.deepcopy(dict(snapshot))
    target = adjusted.get("pull_request")
    if isinstance(target, Mapping):
        adjusted["pull_request"] = _rewrite_manifest_for_core(target)
    prs = adjusted.get("open_pull_requests")
    if isinstance(prs, list):
        adjusted["open_pull_requests"] = [
            _rewrite_manifest_for_core(pr) if isinstance(pr, Mapping) else pr
            for pr in prs
        ]
    return adjusted


def _current_pr_numbers(snapshot: Mapping[str, Any]) -> set[int]:
    result: set[int] = set()
    for issue in snapshot.get("open_work_items") or []:
        if not isinstance(issue, Mapping):
            continue
        current = model._current_pr_number(
            model._issue_control(str(issue.get("body") or ""))
        )
        if current is not None:
            result.add(current)
    target = snapshot.get("pull_request")
    if isinstance(target, Mapping):
        result.add(model._pr_number(target))
    return result


def _warning_findings(snapshot: Mapping[str, Any]) -> list[Finding]:
    target = snapshot.get("pull_request")
    if not isinstance(target, Mapping):
        return []
    target_number = model._pr_number(target)
    target_writes, target_reads = _access_paths(target)
    target_actual = set(model._changed_files(target))
    warnings: list[Finding] = []
    current_numbers = _current_pr_numbers(snapshot)

    for other in snapshot.get("open_pull_requests") or []:
        if not isinstance(other, Mapping):
            continue
        other_number = model._pr_number(other)
        if other_number == target_number or other_number not in current_numbers:
            continue
        other_writes, other_reads = _access_paths(other)
        other_actual = set(model._changed_files(other))
        actual_collision = bool(target_actual & other_actual)

        broad_pairs: list[str] = []
        for left in target_writes:
            for right in other_writes:
                if (
                    (_is_broad_path(left) or _is_broad_path(right))
                    and model._path_specs_overlap(left, right)
                ):
                    broad_pairs.append(f"{left}<->{right}")
        if broad_pairs and not actual_collision:
            warnings.append(
                Finding(
                    "warning",
                    "broad_write_path_overlap",
                    f"pr:{target_number}",
                    (f"other:pr:{other_number}", *tuple(broad_pairs[:5])),
                )
            )

        read_write_pairs: list[str] = []
        other_write_specs = tuple(other_writes) + tuple(other_actual)
        target_write_specs = tuple(target_writes) + tuple(target_actual)
        for read_spec in target_reads:
            for write_spec in other_write_specs:
                if model._path_specs_overlap(read_spec, write_spec):
                    read_write_pairs.append(f"read:{read_spec}<->write:{write_spec}")
        for write_spec in target_write_specs:
            for read_spec in other_reads:
                if model._path_specs_overlap(write_spec, read_spec):
                    read_write_pairs.append(f"write:{write_spec}<->read:{read_spec}")
        if read_write_pairs:
            warnings.append(
                Finding(
                    "warning",
                    "read_write_path_overlap",
                    f"pr:{target_number}",
                    (f"other:pr:{other_number}", *tuple(read_write_pairs[:5])),
                )
            )
    return warnings


def _append_warnings(report: dict[str, Any], warnings: Sequence[Finding]) -> dict[str, Any]:
    if not warnings:
        return report
    findings = list(report.get("findings") or [])
    existing = {
        (str(item.get("code")), str(item.get("subject")))
        for item in findings
        if isinstance(item, Mapping)
    }
    for warning in warnings:
        key = (warning.code, warning.subject)
        if key in existing or len(findings) >= model.MAX_FINDINGS:
            continue
        findings.append(warning.as_dict())
        existing.add(key)
    report["findings"] = findings
    errors = sorted(
        {
            str(item.get("code"))
            for item in findings
            if isinstance(item, Mapping) and item.get("severity") == "error"
        }
    )
    warning_codes = sorted(
        {
            str(item.get("code"))
            for item in findings
            if isinstance(item, Mapping) and item.get("severity") == "warning"
        }
    )
    counts = dict(report.get("counts") or {})
    counts["errors"] = sum(
        isinstance(item, Mapping) and item.get("severity") == "error"
        for item in findings
    )
    counts["warnings"] = sum(
        isinstance(item, Mapping) and item.get("severity") == "warning"
        for item in findings
    )
    report["counts"] = counts
    report["reason_codes"] = errors + warning_codes
    report["state"] = "fail" if errors else ("warn" if warning_codes else "pass")
    return report


def _snapshot_from_event_policy(
    self: gate.GitHubReader, event_path: Path, now: dt.datetime
) -> dict[str, Any]:
    snapshot = _ORIGINAL_SNAPSHOT_FROM_EVENT(self, event_path, now)
    try:
        event = json.loads(event_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateInputError("pull_request_event_invalid") from exc
    snapshot["event_action"] = str(event.get("action") or "synchronize")
    snapshot = hydrate_blocker_authority(snapshot, self.issue)
    return snapshot


def _evaluate_snapshot_policy(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    global _EVALUATION_ALLOW_DELIVERED, _EVALUATION_RUN_ID
    install_runtime_policy()
    original_snapshot = copy.deepcopy(dict(snapshot))
    adjusted = _rewrite_open_blockers(original_snapshot)
    target = adjusted.get("pull_request")
    work_item = adjusted.get("work_item")
    if not isinstance(target, Mapping) or not isinstance(work_item, Mapping):
        raise GateInputError("coordination_snapshot_target_invalid")
    manifest = parse_manifest(target)
    comments = [
        comment
        for comment in (work_item.get("comments") or [])
        if isinstance(comment, Mapping)
    ]
    delivered = _delivered_run_ids(comments)
    action = str(adjusted.get("event_action") or "synchronize").lower()
    _EVALUATION_RUN_ID = manifest.agent_run_id
    _EVALUATION_ALLOW_DELIVERED = (
        action in _NON_WRITING_ACTIONS and manifest.agent_run_id in delivered
    )
    try:
        core_snapshot = _prepare_access_snapshot(adjusted)
        report = _ORIGINAL_EVALUATE_SNAPSHOT(core_snapshot)
        return _append_warnings(report, _warning_findings(original_snapshot))
    finally:
        _EVALUATION_RUN_ID = None
        _EVALUATION_ALLOW_DELIVERED = False


def install_runtime_policy() -> None:
    """Install accepted compatibility and server-evidence rules idempotently."""

    model._RUN_ID_RE = _ACCEPTED_RUN_ID_RE
    model._HEADING_RE = _ACCEPTED_HEADING_RE
    model.LeaseState.active_at = _lease_authorized_at
    model.parse_leases = _parse_leases_policy
    core.parse_leases = _parse_leases_policy
    core._active_lease = _active_lease_policy
    core._resource_intersection = _canonical_resource_intersection
    gate.GitHubReader.snapshot_from_event = _snapshot_from_event_policy
    gate.evaluate_snapshot = _evaluate_snapshot_policy


install_runtime_policy()


def load_snapshot_with_reclaim(
    path: Path | None, now_override: str | None
) -> dict[str, Any]:
    install_runtime_policy()
    snapshot = _ORIGINAL_LOAD_SNAPSHOT(path, now_override)
    return apply_reclaim_implementation_start(
        snapshot,
        event_updated_at=None if path is not None else _event_pr_updated_at(),
    )


def main(argv: Sequence[str] | None = None) -> int:
    install_runtime_policy()
    gate.load_snapshot = load_snapshot_with_reclaim
    gate.evaluate_snapshot = _evaluate_snapshot_policy
    return gate.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
