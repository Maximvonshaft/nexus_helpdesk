"""Pure evaluation and bounded report rendering for the Nexus OSR coordination gate."""
from __future__ import annotations

import json
from typing import Any, Mapping

from agent_coordination_model import (
    MAX_FINDINGS, MAX_REPORT_BYTES, REPORT_SCHEMA, SNAPSHOT_SCHEMA,
    GateInputError, Finding, Manifest, ReportBuilder,
    _active_lease, _as_list, _as_mapping, _blockers, _bounded_text,
    _changed_files, _current_pr_number, _issue_control, _issue_labels,
    _issue_number, _parse_time, _path_matches, _path_specs_overlap,
    _pr_number, _resource_intersection, _work_item_numbers, parse_leases,
    parse_manifest,
)


def evaluate_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    if snapshot.get("schema") != SNAPSHOT_SCHEMA:
        raise GateInputError("snapshot_schema_invalid")
    now = _parse_time(snapshot.get("now"), field_name="snapshot_now")
    target_pr = _as_mapping(snapshot.get("pull_request"), field_name="pull_request")
    work_item = _as_mapping(snapshot.get("work_item"), field_name="work_item")
    open_issues = [_as_mapping(value, field_name="open_work_item") for value in _as_list(snapshot.get("open_work_items"), field_name="open_work_items")]
    open_prs = [_as_mapping(value, field_name="open_pull_request") for value in _as_list(snapshot.get("open_pull_requests"), field_name="open_pull_requests")]
    target_pr_number = _pr_number(target_pr)
    work_item_number = _issue_number(work_item)
    report = ReportBuilder(target_pr_number, work_item_number)

    try:
        manifest = parse_manifest(target_pr)
    except GateInputError as exc:
        report.add("error", str(exc), f"pr:{target_pr_number}")
        return report.build()

    if manifest.work_item != work_item_number:
        report.add("error", "manifest_work_item_mismatch", f"pr:{target_pr_number}", f"manifest:{manifest.work_item}", f"issue:{work_item_number}")

    body_links = _work_item_numbers(str(target_pr.get("body") or ""))
    if body_links and set(body_links) != {work_item_number}:
        report.add("error", "pr_links_multiple_work_items", f"pr:{target_pr_number}", *(f"issue:{number}" for number in body_links[:8]))

    control = _issue_control(str(work_item.get("body") or ""))
    if str(work_item.get("state") or "").lower() != "open":
        report.add("error", "work_item_not_open", f"issue:{work_item_number}")
    if "osr-work-order" not in _issue_labels(work_item):
        report.add("error", "work_item_label_missing", f"issue:{work_item_number}")
    lifecycle = control.get("lifecycle", "").strip().lower()
    if lifecycle not in {"ready", "in progress", "in review", "release gate"}:
        report.add("error", "work_item_not_executable", f"issue:{work_item_number}", f"lifecycle:{lifecycle or 'missing'}")

    open_issue_by_number = {_issue_number(issue): issue for issue in open_issues}
    open_pr_by_number = {_pr_number(pr): pr for pr in open_prs}
    if target_pr_number not in open_pr_by_number:
        open_pr_by_number[target_pr_number] = target_pr

    closing_prs = []
    for pr in open_pr_by_number.values():
        if work_item_number in _work_item_numbers(str(pr.get("body") or "")):
            closing_prs.append(_pr_number(pr))
    if len(closing_prs) != 1:
        report.add("error", "duplicate_or_missing_work_item_pr", f"issue:{work_item_number}", *(f"pr:{number}" for number in sorted(closing_prs)[:8]))

    current_pr = _current_pr_number(control)
    if current_pr != target_pr_number:
        report.add("error", "current_pr_mismatch", f"issue:{work_item_number}", f"expected:pr:{target_pr_number}", f"recorded:{current_pr or 'none'}")

    blockers = tuple(number for number in _blockers(control) if number in open_issue_by_number)
    if blockers:
        if manifest.dependency_mode != "stacked" or manifest.stack_parent_pr is None:
            report.add("error", "unmet_blockers", f"issue:{work_item_number}", *(f"issue:{number}" for number in blockers[:8]))
        else:
            parent = open_pr_by_number.get(manifest.stack_parent_pr)
            if parent is None:
                report.add("error", "stack_parent_pr_not_open", f"pr:{target_pr_number}", f"parent:pr:{manifest.stack_parent_pr}")
            else:
                parent_items = set(_work_item_numbers(str(parent.get("body") or "")))
                unresolved = [number for number in blockers if number not in parent_items]
                if unresolved:
                    report.add("error", "stack_parent_does_not_cover_blockers", f"pr:{target_pr_number}", *(f"issue:{number}" for number in unresolved[:8]))
                parent_head = str(parent.get("head_ref") or "")
                if str(target_pr.get("base_ref") or "") != parent_head:
                    report.add("error", "stack_base_mismatch", f"pr:{target_pr_number}", f"expected_base:{parent_head or 'missing'}")

    comments = [_as_mapping(value, field_name="comment") for value in _as_list(work_item.get("comments"), field_name="work_item_comments")]
    leases, lease_findings = parse_leases(comments)
    report.findings.extend(lease_findings[: max(0, MAX_FINDINGS - len(report.findings))])
    active = _active_lease(leases, now)
    if active is None:
        report.add("error", "active_claim_missing_or_expired", f"issue:{work_item_number}")
    elif active.run_id != manifest.agent_run_id:
        report.add("error", "agent_run_not_active_claim", f"pr:{target_pr_number}", f"manifest_run:{manifest.agent_run_id}", f"active_run:{active.run_id}")

    pr_created_at = _parse_time(target_pr.get("created_at"), field_name="pr_created_at")
    run_lease = leases.get(manifest.agent_run_id)
    if run_lease is None or not run_lease.active_at(pr_created_at):
        report.add("error", "claim_not_valid_at_pr_creation", f"pr:{target_pr_number}", f"run:{manifest.agent_run_id}")

    actual_paths = _changed_files(target_pr)
    undeclared = [path for path in actual_paths if not any(_path_matches(path, spec) for spec in manifest.all_path_specs)]
    if undeclared:
        report.add("error", "actual_path_not_declared", f"pr:{target_pr_number}", *(path for path in undeclared[:8]))

    issue_current_prs: dict[int, int] = {}
    for issue in open_issues:
        number = _issue_number(issue)
        current = _current_pr_number(_issue_control(str(issue.get("body") or "")))
        if current is not None:
            issue_current_prs[current] = number

    compared: list[tuple[Mapping[str, Any], Manifest]] = []
    for number, pr in open_pr_by_number.items():
        if number == target_pr_number:
            continue
        if number not in issue_current_prs:
            report.ignored_historical_prs += 1
            continue
        try:
            other_manifest = parse_manifest(pr)
        except GateInputError:
            report.add("warning", "current_pr_manifest_unparseable", f"pr:{number}")
            other_manifest = Manifest(
                work_item=issue_current_prs[number],
                agent_run_id="unparsed",
                dependency_mode="independent",
                stack_parent_pr=None,
                write_paths=_changed_files(pr),
                contracts=(),
                database=(),
                migrations=(),
                generated_files=tuple(path for path in _changed_files(pr) if path.startswith(".github/") or path.startswith("generated/")),
                workflows=tuple(path for path in _changed_files(pr) if path.startswith(".github/workflows/")),
            )
        compared.append((pr, other_manifest))
    report.compared_current_prs = len(compared)

    target_migration_files = {path for path in actual_paths if "/versions/" in path or "migration" in path.lower()}
    for other_pr, other_manifest in compared:
        other_number = _pr_number(other_pr)
        other_paths = _changed_files(other_pr)
        path_conflicts: set[str] = set(actual_paths) & set(other_paths)
        for target_spec in manifest.all_path_specs:
            for other_spec in other_manifest.all_path_specs:
                if _path_specs_overlap(target_spec, other_spec):
                    path_conflicts.add(f"declared:{target_spec}")
        if path_conflicts:
            report.add("error", "exclusive_write_path_conflict", f"pr:{target_pr_number}", f"other:pr:{other_number}", *(sorted(path_conflicts)[:6]))

        for code, left, right in (
            ("contract_ownership_conflict", manifest.contracts, other_manifest.contracts),
            ("database_ownership_conflict", manifest.database, other_manifest.database),
            ("generated_file_conflict", manifest.generated_files, other_manifest.generated_files),
            ("workflow_conflict", manifest.workflows, other_manifest.workflows),
        ):
            intersection = sorted(_resource_intersection(left, right))
            if intersection:
                report.add("error", code, f"pr:{target_pr_number}", f"other:pr:{other_number}", *intersection[:6])

        other_migration_files = {path for path in other_paths if "/versions/" in path or "migration" in path.lower()}
        if target_migration_files and other_migration_files:
            common_revisions = sorted(_resource_intersection(manifest.migrations, other_manifest.migrations))
            if common_revisions:
                report.add("error", "migration_down_revision_conflict", f"pr:{target_pr_number}", f"other:pr:{other_number}", *common_revisions[:6])
            elif not manifest.migrations or not other_manifest.migrations:
                report.add("error", "migration_ownership_ambiguous", f"pr:{target_pr_number}", f"other:pr:{other_number}")

    if not actual_paths:
        report.add("warning", "pull_request_has_no_changed_files", f"pr:{target_pr_number}")
    return report.build()


def bounded_report_bytes(report: Mapping[str, Any], max_bytes: int = MAX_REPORT_BYTES) -> bytes:
    encoded = json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    if len(encoded) <= max_bytes:
        return encoded
    fallback = {
        "schema": REPORT_SCHEMA,
        "state": "fail",
        "pull_request": report.get("pull_request"),
        "work_item": report.get("work_item"),
        "reason_codes": ["report_too_large"],
        "counts": {"errors": 1, "warnings": 0, "compared_current_prs": 0, "ignored_historical_prs": 0},
        "findings": [{"severity": "error", "code": "report_too_large", "subject": "coordination-report", "details": []}],
        "bounded": True,
        "redacted": True,
    }
    encoded = json.dumps(fallback, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    if len(encoded) > max_bytes:
        raise GateInputError("max_report_bytes_too_small")
    return encoded


def render_markdown(report: Mapping[str, Any]) -> str:
    state = str(report.get("state") or "fail").upper()
    lines = ["# Agent Coordination Gate", "", f"**State:** `{state}`", ""]
    lines.append(f"- Pull request: `#{report.get('pull_request')}`")
    lines.append(f"- Work Item: `#{report.get('work_item')}`")
    counts = report.get("counts") or {}
    lines.append(f"- Errors: `{counts.get('errors', 0)}`")
    lines.append(f"- Warnings: `{counts.get('warnings', 0)}`")
    lines.append(f"- Current PRs compared: `{counts.get('compared_current_prs', 0)}`")
    lines.append(f"- Historical/non-current PRs ignored: `{counts.get('ignored_historical_prs', 0)}`")
    findings = report.get("findings") or []
    if findings:
        lines.extend(["", "## Findings", ""])
        for finding in findings[:MAX_FINDINGS]:
            lines.append(f"- **{str(finding.get('severity', 'error')).upper()}** `{_bounded_text(finding.get('code'))}` — `{_bounded_text(finding.get('subject'))}`")
    lines.extend(["", "The report contains bounded reason codes and resource identifiers only; raw Issue, PR, comment, and credential payloads are excluded."])
    return "\n".join(lines) + "\n"
