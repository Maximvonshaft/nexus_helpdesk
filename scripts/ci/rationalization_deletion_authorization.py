#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from scripts.ci.check_legacy_surface_registry import load_registry
from scripts.ci.rationalization_discovery import (
    DELETE_DISPOSITIONS,
    DiscoveryError,
    LEDGER_SCHEMA,
    _legacy_domains_for_paths,
    _load_ledger_document,
)

RESULT_SCHEMA = "nexus.osr.rationalization-deletion-authorization.v1"
EVIDENCE_SCHEMA = "nexus.osr.rationalization-deletion-evidence.v1"
MAX_SLICES = 50
MAX_PATHS = 250
MAX_TEXT = 2_000
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ALLOWED_STATES = {"deleted_on_work_branch", "accepted_and_merged"}
WORK_SLICE_FIELDS = {"base_sha", "owner_issue", "path_evidence"}
MERGED_SLICE_FIELDS = {"owner_issue", "path_evidence"}
PATH_EVIDENCE_FIELDS = {
    "finding_id",
    "domain_authorization",
    "runtime_consumer_disposition",
    "test_contract_disposition",
    "build_deploy_disposition",
    "security_privacy_impact",
    "verification",
    "rollback_recovery",
    "anti_reintroduction",
}


class DeletionAuthorizationError(ValueError):
    """Bounded fail-closed destructive rationalization error."""


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DeletionAuthorizationError("evidence_duplicate_key")
        result[key] = value
    return result


def _load_evidence(path: Path) -> Mapping[str, Any]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise DeletionAuthorizationError("evidence_read_error") from exc
    if len(data) > 512_000 or b"\0" in data:
        raise DeletionAuthorizationError("evidence_size_or_binary_invalid")
    try:
        raw = json.loads(data.decode("utf-8"), object_pairs_hook=_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeletionAuthorizationError("evidence_json_invalid") from exc
    if not isinstance(raw, dict) or raw.get("schema") != EVIDENCE_SCHEMA:
        raise DeletionAuthorizationError("evidence_schema_invalid")
    if set(raw) != {"schema", "slices"} or not isinstance(raw.get("slices"), dict):
        raise DeletionAuthorizationError("evidence_root_fields_invalid")
    return raw


def _run_git(repo_root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=check,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise DeletionAuthorizationError("git_evidence_unavailable") from exc


def _head(repo_root: Path) -> str:
    value = _run_git(repo_root, "rev-parse", "HEAD").stdout.strip()
    if not SHA_RE.fullmatch(value):
        raise DeletionAuthorizationError("head_sha_invalid")
    return value


def _commit_exists(repo_root: Path, sha: str) -> bool:
    if not SHA_RE.fullmatch(sha):
        return False
    return _run_git(repo_root, "cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode == 0


def _is_ancestor(repo_root: Path, ancestor: str, descendant: str) -> bool:
    return _run_git(repo_root, "merge-base", "--is-ancestor", ancestor, descendant, check=False).returncode == 0


def _deleted_paths(repo_root: Path, base_sha: str, head_sha: str) -> set[str]:
    if not _commit_exists(repo_root, base_sha):
        raise DeletionAuthorizationError("deletion_base_commit_unavailable")
    output = _run_git(repo_root, "diff", "--name-status", f"{base_sha}...{head_sha}").stdout
    deleted: set[str] = set()
    for raw in output.splitlines():
        parts = raw.split("\t")
        if parts and parts[0] == "D" and len(parts) == 2:
            deleted.add(parts[1])
    return deleted


def _safe_path(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 300:
        raise DeletionAuthorizationError("deletion_path_invalid")
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts or value.startswith((".git/", "/")):
        raise DeletionAuthorizationError("deletion_path_unsafe")
    return value


def _positive_issue(value: Any, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DeletionAuthorizationError(code)
    return value


def _bounded_text(value: Any, code: str, *, minimum: int = 12) -> str:
    if not isinstance(value, str):
        raise DeletionAuthorizationError(code)
    text = value.strip()
    if len(text) < minimum or len(text) > MAX_TEXT:
        raise DeletionAuthorizationError(code)
    return text


def _validate_path_evidence(value: Any, *, path: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != PATH_EVIDENCE_FIELDS:
        raise DeletionAuthorizationError("deletion_path_evidence_fields_invalid")
    result = {
        field: _bounded_text(value[field], f"deletion_{field}_invalid")
        for field in sorted(PATH_EVIDENCE_FIELDS)
    }
    finding = result["finding_id"]
    if not (finding.startswith(("root_", "suspicious_", "duplicate_", "unreachable_", "manual_candidate:"))):
        raise DeletionAuthorizationError("deletion_finding_id_invalid")
    lowered = result["test_contract_disposition"].casefold()
    if ("/test" in f"/{path.casefold()}" or path.casefold().startswith("tests/")) and not any(
        token in lowered for token in ("migrat", "retain", "replace", "intentional")
    ):
        raise DeletionAuthorizationError("deleted_test_migration_disposition_missing")
    return result


def _ledger_slices(ledger_path: Path) -> tuple[Mapping[str, Any], list[dict[str, Any]]]:
    raw = _load_ledger_document(ledger_path)
    if not isinstance(raw, dict) or raw.get("schema") != LEDGER_SCHEMA:
        raise DeletionAuthorizationError("ledger_schema_invalid")
    rows = raw.get("deletion_slices")
    if not isinstance(rows, list) or len(rows) > MAX_SLICES:
        raise DeletionAuthorizationError("deletion_slices_invalid")
    result: list[dict[str, Any]] = []
    ids: set[str] = set()
    all_paths: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise DeletionAuthorizationError("deletion_slice_invalid")
        allowed = {"id", "state", "disposition", "paths", "merge_commit"}
        if not set(row).issubset(allowed) or not {"id", "state", "disposition", "paths"}.issubset(row):
            raise DeletionAuthorizationError("deletion_slice_fields_invalid")
        slice_id = row["id"]
        if not isinstance(slice_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,79}", slice_id):
            raise DeletionAuthorizationError("deletion_slice_id_invalid")
        if slice_id in ids:
            raise DeletionAuthorizationError("deletion_slice_id_duplicate")
        ids.add(slice_id)
        if row["state"] not in ALLOWED_STATES:
            raise DeletionAuthorizationError("deletion_slice_state_invalid")
        if row["disposition"] not in DELETE_DISPOSITIONS:
            raise DeletionAuthorizationError("deletion_slice_disposition_invalid")
        paths = row["paths"]
        if not isinstance(paths, list) or not paths or len(paths) > MAX_PATHS:
            raise DeletionAuthorizationError("deletion_slice_paths_invalid")
        normalized = [_safe_path(path) for path in paths]
        if len(set(normalized)) != len(normalized):
            raise DeletionAuthorizationError("deletion_slice_path_duplicate")
        overlap = all_paths.intersection(normalized)
        if overlap:
            raise DeletionAuthorizationError("deletion_path_claimed_by_multiple_slices")
        all_paths.update(normalized)
        if row["state"] == "accepted_and_merged":
            if set(row) != allowed or not SHA_RE.fullmatch(str(row.get("merge_commit", ""))):
                raise DeletionAuthorizationError("merged_deletion_identity_invalid")
        elif "merge_commit" in row:
            raise DeletionAuthorizationError("work_branch_merge_commit_forbidden")
        result.append({**row, "paths": normalized})
    return raw, result


def validate_repository(
    repo_root: Path,
    ledger_path: Path,
    evidence_path: Path,
    registry_path: Path,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    head_sha = _head(repo_root)
    _raw, slices = _ledger_slices(ledger_path)
    evidence = _load_evidence(evidence_path)
    evidence_slices = evidence["slices"]
    ledger_ids = {row["id"] for row in slices}
    if set(evidence_slices) != ledger_ids:
        raise DeletionAuthorizationError("evidence_slice_set_mismatch")

    registry = load_registry(registry_path)
    work_declared: set[str] = set()
    work_actual: set[str] | None = None
    work_base: str | None = None
    validated_paths = 0
    text_cache: dict[str, str | None] = {}

    for row in slices:
        slice_id = row["id"]
        support = evidence_slices[slice_id]
        expected_fields = WORK_SLICE_FIELDS if row["state"] == "deleted_on_work_branch" else MERGED_SLICE_FIELDS
        if not isinstance(support, dict) or set(support) != expected_fields:
            raise DeletionAuthorizationError("evidence_slice_fields_invalid")
        owner_issue = _positive_issue(support["owner_issue"], "deletion_owner_issue_invalid")
        path_evidence = support["path_evidence"]
        if not isinstance(path_evidence, list) or len(path_evidence) != len(row["paths"]):
            raise DeletionAuthorizationError("deletion_path_evidence_count_mismatch")

        if row["state"] == "deleted_on_work_branch":
            base_sha = support["base_sha"]
            if not isinstance(base_sha, str) or not SHA_RE.fullmatch(base_sha):
                raise DeletionAuthorizationError("deletion_base_sha_invalid")
            if work_base is None:
                work_base = base_sha
                work_actual = _deleted_paths(repo_root, base_sha, head_sha)
            elif work_base != base_sha:
                raise DeletionAuthorizationError("multiple_work_branch_bases_forbidden")
            work_declared.update(row["paths"])
        else:
            merge_commit = row["merge_commit"]
            if not _commit_exists(repo_root, merge_commit) or not _is_ancestor(repo_root, merge_commit, head_sha):
                raise DeletionAuthorizationError("merged_deletion_commit_unreachable")

        for index, path in enumerate(row["paths"]):
            details = _validate_path_evidence(path_evidence[index], path=path)
            domains, registry_owners, routed = _legacy_domains_for_paths(
                repo_root,
                registry,
                [path],
                cache=text_cache,
            )
            if routed and registry_owners and owner_issue not in registry_owners:
                raise DeletionAuthorizationError("protected_domain_owner_mismatch")
            if domains and not any(str(issue) in details["domain_authorization"] for issue in registry_owners):
                raise DeletionAuthorizationError("protected_domain_authorization_missing")
            if (repo_root / path).exists():
                raise DeletionAuthorizationError(
                    "merged_deleted_path_reintroduced" if row["state"] == "accepted_and_merged" else "declared_deleted_path_still_present"
                )
            validated_paths += 1

    if work_actual is None:
        work_actual = set()
    if work_declared != work_actual:
        missing = sorted(work_actual - work_declared)
        extra = sorted(work_declared - work_actual)
        code = "actual_deleted_path_omitted" if missing else "declared_deleted_path_not_in_diff"
        raise DeletionAuthorizationError(f"{code}:missing={len(missing)}:extra={len(extra)}")

    return {
        "schema": RESULT_SCHEMA,
        "ok": True,
        "exact_head": head_sha,
        "slice_count": len(slices),
        "validated_path_count": validated_paths,
        "work_branch_deleted_path_count": len(work_actual),
    }


def run(repo_root: Path, ledger_path: Path, evidence_path: Path, registry_path: Path) -> tuple[int, dict[str, Any]]:
    try:
        return 0, validate_repository(repo_root, ledger_path, evidence_path, registry_path)
    except (DeletionAuthorizationError, DiscoveryError, RuntimeError, ValueError) as exc:
        return 2, {"schema": RESULT_SCHEMA, "ok": False, "error": str(exc)[:240]}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate destructive codebase-rationalization authority.")
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--ledger", type=Path, default=Path("docs/ai/codebase-rationalization-inventory.v1.yaml"))
    parser.add_argument("--evidence", type=Path, default=Path("config/governance/rationalization-deletion-evidence.v1.json"))
    parser.add_argument("--registry", type=Path, default=Path("config/governance/legacy-surface-domains.v1.json"))
    args = parser.parse_args(argv)
    root = args.repo_root.resolve()
    paths = [args.ledger, args.evidence, args.registry]
    ledger, evidence, registry = [path if path.is_absolute() else root / path for path in paths]
    status_code, result = run(root, ledger, evidence, registry)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return status_code


if __name__ == "__main__":
    sys.exit(main())
