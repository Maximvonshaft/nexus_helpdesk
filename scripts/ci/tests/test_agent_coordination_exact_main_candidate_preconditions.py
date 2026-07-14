from __future__ import annotations

import json
import subprocess
from pathlib import Path

from scripts.release import exact_main_candidate_preconditions as gate


ROOT = Path(__file__).resolve().parents[3]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()


def _commit(repo: Path, message: str, content: str) -> str:
    target = repo / "state.txt"
    target.write_text(content, encoding="utf-8")
    _git(repo, "add", "state.txt")
    _git(repo, "-c", "user.name=Gate Test", "-c", "user.email=gate@example.invalid", "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD")


def _repo(tmp_path: Path) -> tuple[Path, str]:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    return tmp_path, _commit(tmp_path, "base", "base")


def _manifest(tmp_path: Path, *, exact_main: str, authorities: dict[str, str]) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema": gate.MANIFEST_SCHEMA,
                "exact_main": exact_main,
                "required_authorities": authorities,
                "historical_candidates": ["0cad026eec02ae3b3623273d04f422ead2bb63e8"],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def test_candidate_requires_exact_default_main_identity(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    authorities = {name: base for name in gate.REQUIRED_AUTHORITIES}
    manifest = _manifest(tmp_path, exact_main=base, authorities=authorities)

    result = gate.evaluate(repo, manifest, candidate_sha=base, default_branch="other")

    assert result["candidate_generation_allowed"] is False
    assert "candidate_not_exact_default_main" in result["failure_codes"]


def test_candidate_requires_every_authority_commit_reachable_from_main(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    _git(repo, "checkout", "-qb", "unmerged")
    unmerged = _commit(repo, "unmerged authority", "unmerged")
    _git(repo, "checkout", "-q", "main")
    authorities = {name: base for name in gate.REQUIRED_AUTHORITIES}
    authorities["canonical_console"] = unmerged
    manifest = _manifest(tmp_path, exact_main=base, authorities=authorities)

    result = gate.evaluate(repo, manifest, candidate_sha=base, default_branch="main")

    assert result["candidate_generation_allowed"] is False
    assert "required_authority_not_reachable" in result["failure_codes"]
    assert result["unreachable_authorities"] == ["canonical_console"]


def test_historical_candidate_can_never_be_reused(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    authorities = {name: base for name in gate.REQUIRED_AUTHORITIES}
    manifest = _manifest(tmp_path, exact_main=base, authorities=authorities)

    result = gate.evaluate(
        repo,
        manifest,
        candidate_sha="0cad026eec02ae3b3623273d04f422ead2bb63e8",
        default_branch="main",
    )

    assert result["candidate_generation_allowed"] is False
    assert "historical_candidate_reuse_forbidden" in result["failure_codes"]


def test_manifest_requires_all_gate_authorities(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    authorities = {name: base for name in gate.REQUIRED_AUTHORITIES if name != "delivery_truth"}
    manifest = _manifest(tmp_path, exact_main=base, authorities=authorities)

    result = gate.evaluate(repo, manifest, candidate_sha=base, default_branch="main")

    assert result["candidate_generation_allowed"] is False
    assert "required_authority_missing" in result["failure_codes"]
    assert result["missing_authorities"] == ["delivery_truth"]


def test_current_repository_candidate_generation_is_blocked_until_convergence() -> None:
    result = gate.evaluate_repository(ROOT)

    assert result["candidate_generation_allowed"] is False
    assert set(result["missing_authorities"]) >= {
        "canonical_console",
        "rationalization",
        "policy_projection",
        "actions",
    }
    assert "candidate_not_exact_default_main" in result["failure_codes"]
