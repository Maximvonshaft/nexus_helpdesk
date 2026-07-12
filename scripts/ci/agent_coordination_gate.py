#!/usr/bin/env python3
"""Read-only GitHub runtime and CLI for the Nexus OSR coordination preflight."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Sequence

from agent_coordination_model import (
    MAX_REPORT_BYTES, REPORT_SCHEMA, SNAPSHOT_SCHEMA, GateInputError,
    _as_mapping, _bounded_text, _current_pr_number, _issue_control,
    _issue_number, _parse_time, _work_item_numbers,
)
from agent_coordination_core import bounded_report_bytes, evaluate_snapshot, render_markdown

# Re-export the public test and integration surface from the split implementation.
from agent_coordination_model import MANIFEST_SCHEMA, ReportBuilder  # noqa: F401


class GitHubReader:
    def __init__(self, repository: str, token: str, api_url: str = "https://api.github.com") -> None:
        if "/" not in repository:
            raise GateInputError("github_repository_invalid")
        self.repository = repository
        self.token = token
        self.api_url = api_url.rstrip("/")

    def get(self, path: str) -> Any:
        url = path if path.startswith("http") else f"{self.api_url}{path}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "nexus-osr-agent-coordination-gate",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            code = getattr(exc, "code", "unavailable")
            raise GateInputError(f"github_api_unavailable:{_bounded_text(code)}") from exc

    def get_paginated(self, path: str) -> list[Any]:
        separator = "&" if "?" in path else "?"
        page = 1
        results: list[Any] = []
        while True:
            payload = self.get(f"{path}{separator}per_page=100&page={page}")
            if not isinstance(payload, list):
                raise GateInputError("github_paginated_response_invalid")
            results.extend(payload)
            if len(payload) < 100:
                break
            page += 1
            if page > 20:
                raise GateInputError("github_pagination_bound_exceeded")
        return results

    def issue(self, number: int) -> dict[str, Any]:
        raw = _as_mapping(self.get(f"/repos/{self.repository}/issues/{number}"), field_name="github_issue")
        comments = self.get_paginated(f"/repos/{self.repository}/issues/{number}/comments")
        return {
            "number": int(raw["number"]),
            "state": raw.get("state"),
            "body": raw.get("body") or "",
            "labels": raw.get("labels") or [],
            "comments": comments,
        }

    def pr(self, number: int, *, include_files: bool) -> dict[str, Any]:
        raw = _as_mapping(self.get(f"/repos/{self.repository}/pulls/{number}"), field_name="github_pr")
        files = self.get_paginated(f"/repos/{self.repository}/pulls/{number}/files") if include_files else []
        return {
            "number": int(raw["number"]),
            "state": raw.get("state"),
            "draft": bool(raw.get("draft")),
            "body": raw.get("body") or "",
            "head_sha": (raw.get("head") or {}).get("sha"),
            "head_ref": (raw.get("head") or {}).get("ref"),
            "base_ref": (raw.get("base") or {}).get("ref"),
            "created_at": raw.get("created_at"),
            "changed_files": [entry.get("filename") for entry in files if isinstance(entry, Mapping)],
        }

    def snapshot_from_event(self, event_path: Path, now: dt.datetime) -> dict[str, Any]:
        try:
            event = json.loads(event_path.read_text(encoding="utf-8"))
            target_number = int(event["pull_request"]["number"])
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise GateInputError("pull_request_event_invalid") from exc
        target_raw = self.pr(target_number, include_files=True)
        work_items = _work_item_numbers(str(target_raw.get("body") or ""))
        if len(work_items) != 1:
            raise GateInputError("pr_must_link_exactly_one_work_item")
        work_item_number = work_items[0]
        issue_rows = self.get_paginated(f"/repos/{self.repository}/issues?state=open&labels=osr-work-order")
        open_work_items: list[dict[str, Any]] = []
        current_pr_numbers: set[int] = {target_number}
        for row in issue_rows:
            if not isinstance(row, Mapping) or "pull_request" in row:
                continue
            issue = {
                "number": int(row["number"]),
                "state": row.get("state"),
                "body": row.get("body") or "",
                "labels": row.get("labels") or [],
                "comments": [],
            }
            open_work_items.append(issue)
            current = _current_pr_number(_issue_control(str(issue["body"])))
            if current is not None:
                current_pr_numbers.add(current)
        target_issue = self.issue(work_item_number)
        if work_item_number not in {_issue_number(issue) for issue in open_work_items}:
            open_work_items.append({key: value for key, value in target_issue.items() if key != "comments"} | {"comments": []})

        pr_rows = self.get_paginated(f"/repos/{self.repository}/pulls?state=open")
        open_pull_requests: list[dict[str, Any]] = []
        for row in pr_rows:
            if not isinstance(row, Mapping):
                continue
            number = int(row["number"])
            body = str(row.get("body") or "")
            include_files = number in current_pr_numbers or work_item_number in _work_item_numbers(body)
            if number == target_number:
                open_pull_requests.append(target_raw)
            elif include_files:
                open_pull_requests.append(self.pr(number, include_files=True))
            else:
                open_pull_requests.append(
                    {
                        "number": number,
                        "state": row.get("state"),
                        "draft": bool(row.get("draft")),
                        "body": body,
                        "head_sha": (row.get("head") or {}).get("sha"),
                        "head_ref": (row.get("head") or {}).get("ref"),
                        "base_ref": (row.get("base") or {}).get("ref"),
                        "created_at": row.get("created_at"),
                        "changed_files": [],
                    }
                )
        return {
            "schema": SNAPSHOT_SCHEMA,
            "now": now.isoformat().replace("+00:00", "Z"),
            "repository": self.repository,
            "pull_request": target_raw,
            "work_item": target_issue,
            "open_work_items": open_work_items,
            "open_pull_requests": open_pull_requests,
        }


def load_snapshot(path: Path | None, now_override: str | None) -> dict[str, Any]:
    if path is not None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise GateInputError("snapshot_unreadable") from exc
        if now_override:
            payload["now"] = _parse_time(now_override, field_name="now_override").isoformat().replace("+00:00", "Z")
        return payload
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not repository or not token or not event_path:
        raise GateInputError("github_runtime_environment_incomplete")
    now = _parse_time(now_override, field_name="now_override") if now_override else dt.datetime.now(dt.timezone.utc)
    return GitHubReader(repository, token, os.environ.get("GITHUB_API_URL", "https://api.github.com")).snapshot_from_event(Path(event_path), now)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, help="Read a deterministic snapshot instead of GitHub API metadata")
    parser.add_argument("--output", type=Path, default=Path("artifacts/agent-coordination/report.json"))
    parser.add_argument("--summary-path", type=Path)
    parser.add_argument("--max-bytes", type=int, default=MAX_REPORT_BYTES)
    parser.add_argument("--now")
    args = parser.parse_args(argv)

    try:
        snapshot = load_snapshot(args.snapshot, args.now)
        report = evaluate_snapshot(snapshot)
        report_bytes = bounded_report_bytes(report, args.max_bytes)
        final_report = json.loads(report_bytes)
    except GateInputError as exc:
        final_report = {
            "schema": REPORT_SCHEMA,
            "state": "fail",
            "pull_request": None,
            "work_item": None,
            "reason_codes": [_bounded_text(exc)],
            "counts": {"errors": 1, "warnings": 0, "compared_current_prs": 0, "ignored_historical_prs": 0},
            "findings": [{"severity": "error", "code": _bounded_text(exc), "subject": "coordination-input", "details": []}],
            "bounded": True,
            "redacted": True,
        }
        report_bytes = bounded_report_bytes(final_report, args.max_bytes)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(report_bytes + b"\n")
    if args.summary_path:
        args.summary_path.parent.mkdir(parents=True, exist_ok=True)
        args.summary_path.write_text(render_markdown(final_report), encoding="utf-8")
    print(json.dumps({
        "schema": final_report.get("schema"),
        "state": final_report.get("state"),
        "pull_request": final_report.get("pull_request"),
        "work_item": final_report.get("work_item"),
        "reason_codes": final_report.get("reason_codes"),
        "bounded": True,
        "redacted": True,
    }, sort_keys=True))
    return 1 if final_report.get("state") == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
