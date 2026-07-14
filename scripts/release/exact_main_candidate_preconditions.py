#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

MANIFEST_SCHEMA = "nexus.osr.release-candidate-preconditions.v1"
RESULT_SCHEMA = "nexus.osr.release-candidate-preconditions-result.v1"
REQUIRED_AUTHORITIES = (
    "canonical_console",
    "rationalization",
    "policy_projection",
    "actions",
    "delivery_truth",
)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class CandidatePreconditionError(ValueError):
    pass


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CandidatePreconditionError("manifest_duplicate_key")
        result[key] = value
    return result


def _load(path: Path) -> Mapping[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_strict_object)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CandidatePreconditionError("manifest_invalid") from exc
    if not isinstance(raw, dict) or set(raw) != {
        "schema", "exact_main", "required_authorities", "historical_candidates"
    }:
        raise CandidatePreconditionError("manifest_fields_invalid")
    if raw.get("schema") != MANIFEST_SCHEMA:
        raise CandidatePreconditionError("manifest_schema_invalid")
    exact_main = raw.get("exact_main")
    if not isinstance(exact_main, str) or not SHA_RE.fullmatch(exact_main):
        raise CandidatePreconditionError("manifest_exact_main_invalid")
    authorities = raw.get("required_authorities")
    if not isinstance(authorities, dict) or not set(authorities).issubset(REQUIRED_AUTHORITIES):
        raise CandidatePreconditionError("manifest_authorities_invalid")
    for name, sha in authorities.items():
        if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
            raise CandidatePreconditionError(f"authority_sha_invalid:{name}")
    historical = raw.get("historical_candidates")
    if not isinstance(historical, list) or len(historical) != len(set(historical)):
        raise CandidatePreconditionError("historical_candidates_invalid")
    if not all(isinstance(sha, str) and SHA_RE.fullmatch(sha) for sha in historical):
        raise CandidatePreconditionError("historical_candidate_sha_invalid")
    return raw


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repo,
            check=check,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CandidatePreconditionError("git_evidence_unavailable") from exc


def _resolve(repo: Path, ref: str) -> str | None:
    result = _git(repo, "rev-parse", "--verify", ref, check=False)
    value = result.stdout.strip()
    return value if result.returncode == 0 and SHA_RE.fullmatch(value) else None


def _commit_exists(repo: Path, sha: str) -> bool:
    return _git(repo, "cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode == 0


def _ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return _git(repo, "merge-base", "--is-ancestor", ancestor, descendant, check=False).returncode == 0


def _default_main(repo: Path, default_branch: str) -> str | None:
    return _resolve(repo, f"refs/remotes/origin/{default_branch}") or _resolve(repo, f"refs/heads/{default_branch}")


def evaluate(
    repo_root: Path,
    manifest_path: Path,
    *,
    candidate_sha: str,
    default_branch: str = "main",
) -> dict[str, Any]:
    repo = repo_root.resolve()
    manifest = _load(manifest_path)
    failure_codes: list[str] = []
    missing = sorted(set(REQUIRED_AUTHORITIES) - set(manifest["required_authorities"]))
    historical = set(manifest["historical_candidates"])

    if candidate_sha in historical:
        failure_codes.append("historical_candidate_reuse_forbidden")
    if not SHA_RE.fullmatch(candidate_sha) or not _commit_exists(repo, candidate_sha):
        failure_codes.append("candidate_commit_invalid")

    runtime_main = _default_main(repo, default_branch)
    if runtime_main is None or candidate_sha != runtime_main:
        failure_codes.append("candidate_not_exact_default_main")
    if runtime_main is None or manifest["exact_main"] != runtime_main:
        failure_codes.append("manifest_exact_main_mismatch")
    if missing:
        failure_codes.append("required_authority_missing")

    unreachable: list[str] = []
    invalid: list[str] = []
    for name, sha in sorted(manifest["required_authorities"].items()):
        if not _commit_exists(repo, sha):
            invalid.append(name)
        elif SHA_RE.fullmatch(candidate_sha) and _commit_exists(repo, candidate_sha) and not _ancestor(repo, sha, candidate_sha):
            unreachable.append(name)
    if invalid:
        failure_codes.append("required_authority_commit_invalid")
    if unreachable:
        failure_codes.append("required_authority_not_reachable")

    deduped = sorted(set(failure_codes))
    allowed = not deduped
    return {
        "schema": RESULT_SCHEMA,
        "ok": allowed,
        "candidate_generation_allowed": allowed,
        "candidate_sha": candidate_sha,
        "runtime_main": runtime_main,
        "manifest_exact_main": manifest["exact_main"],
        "missing_authorities": missing,
        "invalid_authorities": invalid,
        "unreachable_authorities": unreachable,
        "failure_codes": deduped,
    }


def evaluate_repository(repo_root: Path) -> dict[str, Any]:
    repo = repo_root.resolve()
    candidate = _resolve(repo, "HEAD") or ""
    return evaluate(
        repo,
        repo / "config/governance/release-candidate-preconditions.v1.json",
        candidate_sha=candidate,
        default_branch="main",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail closed unless a Release Candidate is exact accepted main.")
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--manifest", type=Path, default=Path("config/governance/release-candidate-preconditions.v1.json"))
    parser.add_argument("--candidate-sha")
    parser.add_argument("--default-branch", default="main")
    args = parser.parse_args(argv)
    root = args.repo_root.resolve()
    candidate = args.candidate_sha or _resolve(root, "HEAD") or ""
    manifest = args.manifest if args.manifest.is_absolute() else root / args.manifest
    try:
        result = evaluate(root, manifest, candidate_sha=candidate, default_branch=args.default_branch)
    except CandidatePreconditionError as exc:
        result = {
            "schema": RESULT_SCHEMA,
            "ok": False,
            "candidate_generation_allowed": False,
            "failure_codes": [str(exc)[:240]],
            "missing_authorities": list(REQUIRED_AUTHORITIES),
        }
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
