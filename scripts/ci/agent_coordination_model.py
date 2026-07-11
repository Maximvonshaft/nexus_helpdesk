#!/usr/bin/env python3
"""Machine-enforced Nexus OSR Work Item and Agent coordination preflight.

The gate consumes a bounded metadata snapshot either from a fixture (``--snapshot``)
or by reading the current pull-request event and GitHub's read-only REST API. It
never executes repository code from other PRs and never emits Issue/PR bodies,
comments, credentials, or arbitrary API errors in its report.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SNAPSHOT_SCHEMA = "nexus.osr.agent_coordination.snapshot.v1"
REPORT_SCHEMA = "nexus.osr.agent_coordination.report.v1"
MANIFEST_SCHEMA = "nexus.osr.coordination.manifest.v1"
MAX_REPORT_BYTES = 65_536
MAX_FINDINGS = 50
MAX_DETAIL_LENGTH = 160
DEFAULT_LEASE_MINUTES = 120

_CONTROL_RE = re.compile(r"(?mi)^-\s*(?P<key>Parent Epic|Lifecycle|Owner|Current PR|Blocked by|Supersedes):\s*(?P<value>.+?)\s*$")
_WORK_ITEM_RE = re.compile(r"(?im)(?:\bCloses\s*:?[ \t]*|\bWork Item\s*:\s*)#(?P<number>\d+)\b")
_RUN_ID_RE = re.compile(r"(?mi)^-\s*(?:Agent Run ID|Run ID|New Run ID):\s*`?(?P<value>[^`\n]+)`?\s*$")
_STACK_RE = re.compile(r"(?mi)^-\s*Dependency mode:\s*stacked(?:\s+on)?\s+(?:PR\s*)?#(?P<number>\d+)\b")
_CURRENT_PR_RE = re.compile(r"#(?P<number>\d+)")
_ISSUE_NUMBER_RE = re.compile(r"#(?P<number>\d+)")
_HEADING_RE = re.compile(r"(?m)^##\s+(?P<heading>AGENT_CLAIM|AGENT_HEARTBEAT|AGENT_HANDOFF|AGENT_RECLAIM)\s*$")
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d(?!\w)")
_SECRET_RE = re.compile(r"(?i)\b(?:gh[pousr]_[A-Za-z0-9_]{8,}|(?:api|access|refresh)[_-]?token\s*[:=]\s*\S+|bearer\s+\S+)")
_LONG_ID_RE = re.compile(r"\b(?=[A-Za-z0-9_-]{20,}\b)(?=[A-Za-z0-9_-]*[A-Za-z])(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]+\b")
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_./*#:+-]+$")


class GateInputError(ValueError):
    """Raised when required coordination metadata is missing or malformed."""


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    subject: str
    details: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "subject": _bounded_text(self.subject),
            "details": [_bounded_text(value) for value in self.details[:8]],
        }


@dataclass
class ReportBuilder:
    pr_number: int
    work_item_number: int
    findings: list[Finding] = field(default_factory=list)
    ignored_historical_prs: int = 0
    compared_current_prs: int = 0

    def add(self, severity: str, code: str, subject: str, *details: str) -> None:
        if len(self.findings) >= MAX_FINDINGS:
            return
        self.findings.append(Finding(severity, code, subject, tuple(details)))

    def build(self) -> dict[str, Any]:
        errors = sorted({item.code for item in self.findings if item.severity == "error"})
        warnings = sorted({item.code for item in self.findings if item.severity == "warning"})
        state = "fail" if errors else ("warn" if warnings else "pass")
        return {
            "schema": REPORT_SCHEMA,
            "state": state,
            "pull_request": self.pr_number,
            "work_item": self.work_item_number,
            "reason_codes": errors + warnings,
            "counts": {
                "errors": sum(item.severity == "error" for item in self.findings),
                "warnings": sum(item.severity == "warning" for item in self.findings),
                "compared_current_prs": self.compared_current_prs,
                "ignored_historical_prs": self.ignored_historical_prs,
            },
            "findings": [item.as_dict() for item in self.findings],
            "bounded": True,
            "redacted": True,
        }


@dataclass(frozen=True)
class Manifest:
    work_item: int
    agent_run_id: str
    dependency_mode: str
    stack_parent_pr: int | None
    write_paths: tuple[str, ...]
    contracts: tuple[str, ...]
    database: tuple[str, ...]
    migrations: tuple[str, ...]
    generated_files: tuple[str, ...]
    workflows: tuple[str, ...]

    @property
    def all_path_specs(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self.write_paths + self.generated_files + self.workflows))


@dataclass(frozen=True)
class LeaseEvent:
    kind: str
    run_id: str
    created_at: dt.datetime
    released: bool = False


@dataclass(frozen=True)
class LeaseState:
    run_id: str
    started_at: dt.datetime
    expires_at: dt.datetime
    released_at: dt.datetime | None

    def active_at(self, instant: dt.datetime) -> bool:
        return self.started_at <= instant < self.expires_at and self.released_at is None


def _parse_time(value: Any, *, field_name: str) -> dt.datetime:
    if not isinstance(value, str) or not value.strip():
        raise GateInputError(f"{field_name}_required")
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise GateInputError(f"{field_name}_invalid") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _bounded_text(value: Any) -> str:
    text = str(value or "")
    text = _EMAIL_RE.sub("[redacted-email]", text)
    text = _PHONE_RE.sub("[redacted-phone]", text)
    text = _SECRET_RE.sub("[redacted-secret]", text)
    text = _LONG_ID_RE.sub("[redacted-id]", text)
    text = " ".join(text.split())
    return text[:MAX_DETAIL_LENGTH]


def _safe_token(value: Any) -> str | None:
    text = str(value or "").strip().strip("`'\"")
    if not text or len(text) > MAX_DETAIL_LENGTH or not _SAFE_TOKEN_RE.fullmatch(text):
        return None
    if _EMAIL_RE.search(text) or _PHONE_RE.search(text) or _SECRET_RE.search(text):
        return None
    return text


def _as_mapping(value: Any, *, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GateInputError(f"{field_name}_must_be_object")
    return value


def _as_list(value: Any, *, field_name: str) -> list[Any]:
    if not isinstance(value, list):
        raise GateInputError(f"{field_name}_must_be_array")
    return value


def _issue_control(body: str) -> dict[str, str]:
    return {match.group("key").lower().replace(" ", "_"): match.group("value").strip() for match in _CONTROL_RE.finditer(body or "")}


def _issue_labels(issue: Mapping[str, Any]) -> set[str]:
    labels: set[str] = set()
    for value in issue.get("labels") or []:
        if isinstance(value, str):
            labels.add(value)
        elif isinstance(value, Mapping) and isinstance(value.get("name"), str):
            labels.add(str(value["name"]))
    return labels


def _work_item_numbers(body: str) -> tuple[int, ...]:
    return tuple(dict.fromkeys(int(match.group("number")) for match in _WORK_ITEM_RE.finditer(body or "")))


def _run_id(body: str) -> str:
    match = _RUN_ID_RE.search(body or "")
    return match.group("value").strip() if match else ""


def _json_manifest(body: str) -> Mapping[str, Any] | None:
    marker = MANIFEST_SCHEMA
    if marker not in (body or ""):
        return None
    for match in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", body or "", re.DOTALL | re.IGNORECASE):
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, Mapping) and payload.get("schema") == marker:
            return payload
    raise GateInputError("coordination_manifest_invalid_json")


def _manifest_tokens(payload: Mapping[str, Any], key: str) -> tuple[str, ...]:
    raw = payload.get(key, [])
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise GateInputError(f"manifest_{key}_must_be_array")
    values: list[str] = []
    for entry in raw:
        token = _safe_token(entry)
        if token is None:
            raise GateInputError(f"manifest_{key}_unsafe_token")
        values.append(token)
    return tuple(dict.fromkeys(values))


def _fallback_resource_tokens(body: str, heading: str) -> tuple[str, ...]:
    pattern = re.compile(rf"(?ms)^-\s*{re.escape(heading)}:\s*(.*?)(?=^\s*-\s*[A-Z][^\n:]+:|^##|\Z)")
    match = pattern.search(body or "")
    if not match:
        return ()
    block = match.group(1)
    tokens: list[str] = []
    for quoted in re.findall(r"`([^`]+)`", block):
        token = _safe_token(quoted)
        if token:
            tokens.append(token)
    for line in block.splitlines():
        candidate = line.strip().lstrip("- ").strip()
        token = _safe_token(candidate)
        if token and ("/" in token or token.lower() in {"none", "n/a", "not_applicable"}):
            tokens.append(token)
    return tuple(dict.fromkeys(tokens))


def parse_manifest(pr: Mapping[str, Any]) -> Manifest:
    body = str(pr.get("body") or "")
    payload = _json_manifest(body)
    work_items = _work_item_numbers(body)
    if payload is not None:
        try:
            work_item = int(payload["work_item"])
        except (KeyError, TypeError, ValueError) as exc:
            raise GateInputError("manifest_work_item_invalid") from exc
        agent_run_id = str(payload.get("agent_run_id") or "").strip()
        dependency = payload.get("dependency") or {}
        if not isinstance(dependency, Mapping):
            raise GateInputError("manifest_dependency_must_be_object")
        dependency_mode = str(dependency.get("mode") or "independent").strip().lower()
        parent_raw = dependency.get("stack_parent_pr")
        stack_parent = None if parent_raw in (None, "", 0) else int(parent_raw)
        manifest = Manifest(
            work_item=work_item,
            agent_run_id=agent_run_id,
            dependency_mode=dependency_mode,
            stack_parent_pr=stack_parent,
            write_paths=_manifest_tokens(payload, "write_paths"),
            contracts=_manifest_tokens(payload, "contracts"),
            database=_manifest_tokens(payload, "database"),
            migrations=_manifest_tokens(payload, "migrations"),
            generated_files=_manifest_tokens(payload, "generated_files"),
            workflows=_manifest_tokens(payload, "workflows"),
        )
    else:
        if len(work_items) != 1:
            raise GateInputError("pr_must_link_exactly_one_work_item")
        stack_match = _STACK_RE.search(body)
        manifest = Manifest(
            work_item=work_items[0],
            agent_run_id=_run_id(body),
            dependency_mode="stacked" if stack_match else "independent",
            stack_parent_pr=int(stack_match.group("number")) if stack_match else None,
            write_paths=_fallback_resource_tokens(body, "Declared write paths"),
            contracts=_fallback_resource_tokens(body, "Contracts changed"),
            database=_fallback_resource_tokens(body, "Database tables/columns/migrations"),
            migrations=tuple(
                token
                for token in (
                    _safe_token(value)
                    for value in re.findall(r"(?mi)^-\s*(?:Schema migration|Down revision):\s*`?([^`\n]+)`?", body)
                )
                if token and token.lower() not in {"none", "not_applicable", "n/a"}
            ),
            generated_files=_fallback_resource_tokens(body, "Workflows/generated files/external mutable resources"),
            workflows=tuple(
                token
                for token in _fallback_resource_tokens(body, "Workflows/generated files/external mutable resources")
                if token.startswith(".github/workflows/")
            ),
        )
    if not manifest.agent_run_id:
        raise GateInputError("agent_run_id_required")
    if manifest.dependency_mode not in {"independent", "stacked"}:
        raise GateInputError("dependency_mode_invalid")
    if manifest.dependency_mode == "stacked" and manifest.stack_parent_pr is None:
        raise GateInputError("stack_parent_pr_required")
    if manifest.dependency_mode == "independent" and manifest.stack_parent_pr is not None:
        raise GateInputError("independent_pr_cannot_have_stack_parent")
    if not manifest.write_paths:
        raise GateInputError("declared_write_paths_required")
    return manifest


def _comment_event(comment: Mapping[str, Any]) -> LeaseEvent | None:
    body = str(comment.get("body") or "")
    heading = _HEADING_RE.search(body)
    if not heading:
        return None
    run_id = _run_id(body)
    if not run_id:
        return None
    created_at = _parse_time(comment.get("created_at"), field_name="comment_created_at")
    released = bool(re.search(r"(?mi)^-\s*Claim released:\s*yes\s*$", body))
    return LeaseEvent(heading.group("heading"), run_id, created_at, released)


def parse_leases(comments: Sequence[Mapping[str, Any]]) -> tuple[dict[str, LeaseState], list[Finding]]:
    events = sorted((event for comment in comments if (event := _comment_event(comment))), key=lambda item: item.created_at)
    leases: dict[str, LeaseState] = {}
    findings: list[Finding] = []
    active_run: str | None = None
    for event in events:
        if event.kind in {"AGENT_CLAIM", "AGENT_RECLAIM"}:
            if active_run:
                prior = leases[active_run]
                prior_released = prior.released_at is not None and prior.released_at <= event.created_at
                prior_expired = prior.expires_at <= event.created_at
                if event.kind == "AGENT_CLAIM" and not (prior_released or prior_expired):
                    findings.append(Finding("error", "overlapping_active_claim", f"run:{event.run_id}", (f"winning_run:{active_run}",)))
                    continue
                if event.kind == "AGENT_RECLAIM" and not (prior_released or prior_expired):
                    findings.append(Finding("error", "reclaim_before_release_or_expiry", f"run:{event.run_id}", (f"prior:{active_run}",)))
                    continue
            active_run = event.run_id
            leases[event.run_id] = LeaseState(
                run_id=event.run_id,
                started_at=event.created_at,
                expires_at=event.created_at + dt.timedelta(minutes=DEFAULT_LEASE_MINUTES),
                released_at=None,
            )
            continue
        if event.run_id not in leases:
            findings.append(Finding("error", "lease_event_without_claim", f"run:{event.run_id}", (event.kind.lower(),)))
            continue
        current = leases[event.run_id]
        if event.kind == "AGENT_HEARTBEAT":
            if current.released_at is not None:
                findings.append(Finding("error", "heartbeat_after_release", f"run:{event.run_id}"))
            elif event.created_at > current.expires_at:
                findings.append(Finding("error", "heartbeat_after_lease_expiry", f"run:{event.run_id}"))
            else:
                leases[event.run_id] = LeaseState(
                    run_id=current.run_id,
                    started_at=current.started_at,
                    expires_at=event.created_at + dt.timedelta(minutes=DEFAULT_LEASE_MINUTES),
                    released_at=None,
                )
        elif event.kind == "AGENT_HANDOFF" and event.released:
            leases[event.run_id] = LeaseState(
                run_id=current.run_id,
                started_at=current.started_at,
                expires_at=min(current.expires_at, event.created_at),
                released_at=event.created_at,
            )
            if active_run == event.run_id:
                active_run = None
    return leases, findings


def _active_lease(leases: Mapping[str, LeaseState], now: dt.datetime) -> LeaseState | None:
    active = [lease for lease in leases.values() if lease.active_at(now)]
    if not active:
        return None
    return max(active, key=lambda lease: lease.started_at)


def _normalize_path_spec(value: str) -> str:
    return value.strip().lstrip("./")


def _path_matches(path: str, spec: str) -> bool:
    path = _normalize_path_spec(path)
    spec = _normalize_path_spec(spec)
    if spec.endswith("/**"):
        prefix = spec[:-3].rstrip("/")
        return path == prefix or path.startswith(prefix + "/")
    if spec.endswith("/"):
        return path.startswith(spec)
    if any(character in spec for character in "*?["):
        return fnmatch.fnmatchcase(path, spec)
    return path == spec


def _path_specs_overlap(left: str, right: str) -> bool:
    left = _normalize_path_spec(left)
    right = _normalize_path_spec(right)
    candidates = {left.rstrip("/*"), right.rstrip("/*")}
    for candidate in list(candidates):
        if candidate:
            candidates.add(candidate + "/probe")
    return any(_path_matches(candidate, left) and _path_matches(candidate, right) for candidate in candidates)


def _resource_intersection(left: Iterable[str], right: Iterable[str]) -> set[str]:
    left_set = {_bounded_text(value).lower() for value in left if value and value.lower() not in {"none", "n/a", "not_applicable"}}
    right_set = {_bounded_text(value).lower() for value in right if value and value.lower() not in {"none", "n/a", "not_applicable"}}
    return left_set & right_set


def _changed_files(pr: Mapping[str, Any]) -> tuple[str, ...]:
    values = pr.get("changed_files") or []
    if not isinstance(values, list):
        raise GateInputError("changed_files_must_be_array")
    safe: list[str] = []
    for value in values:
        token = _safe_token(value)
        if token is None:
            raise GateInputError("unsafe_changed_file_path")
        safe.append(_normalize_path_spec(token))
    return tuple(dict.fromkeys(safe))


def _current_pr_number(control: Mapping[str, str]) -> int | None:
    value = control.get("current_pr", "")
    match = _CURRENT_PR_RE.search(value)
    return int(match.group("number")) if match else None


def _blockers(control: Mapping[str, str]) -> tuple[int, ...]:
    value = control.get("blocked_by", "")
    if value.strip().lower() in {"", "none", "n/a"}:
        return ()
    return tuple(dict.fromkeys(int(match.group("number")) for match in _ISSUE_NUMBER_RE.finditer(value)))


def _pr_number(pr: Mapping[str, Any]) -> int:
    try:
        return int(pr["number"])
    except (KeyError, TypeError, ValueError) as exc:
        raise GateInputError("pr_number_invalid") from exc


def _issue_number(issue: Mapping[str, Any]) -> int:
    try:
        return int(issue["number"])
    except (KeyError, TypeError, ValueError) as exc:
        raise GateInputError("issue_number_invalid") from exc
