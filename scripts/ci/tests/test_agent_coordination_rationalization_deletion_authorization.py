from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.ci import rationalization_deletion_authorization as gate


ROOT = Path(__file__).resolve().parents[3]
REGISTRY = ROOT / "config/governance/legacy-surface-domains.v1.json"


def _git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=Gate Test", "-c", "user.email=gate@example.invalid", "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD")


def _repo(tmp_path: Path, path: str = "obsolete.py") -> tuple[Path, str, str]:
    _git(tmp_path, "init", "-q", "-b", "main")
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("obsolete = True\n", encoding="utf-8")
    base = _commit(tmp_path, "base")
    target.unlink()
    head = _commit(tmp_path, "delete obsolete path")
    return tmp_path, base, head


def _path_evidence(*, finding_id: str = "manual_candidate:obsolete.py", test_disposition: str = "Retained canonical tests replace this duplicate coverage.") -> dict[str, str]:
    return {
        "finding_id": finding_id,
        "domain_authorization": "Issue #744 authorizes this bounded deletion after domain-owner and consumer review.",
        "runtime_consumer_disposition": "No runtime, API, worker, script, migration or deployment consumer remains.",
        "test_contract_disposition": test_disposition,
        "build_deploy_disposition": "Compile, regression and release-image gates validate the surviving entry graph.",
        "security_privacy_impact": "No authentication, authorization, privacy, secret or customer-data boundary is removed.",
        "verification": "Exact base-to-Head diff and focused, regression, security and image checks.",
        "rollback_recovery": "Recover exact bytes from the immutable base commit if a missing consumer is proven.",
        "anti_reintroduction": "Tracked-tree and duplicate scanners require a current owner and consumer before restoration.",
    }


def _write_contract(
    repo: Path,
    *,
    path: str,
    base: str,
    state: str = "deleted_on_work_branch",
    owner_issue: int = 744,
    evidence: dict[str, str] | None = None,
    merge_commit: str | None = None,
) -> tuple[Path, Path]:
    ledger = {
        "schema": "nexus.osr.codebase-rationalization-inventory.v1",
        "discovery_gate": {"contract": "nexus.osr.rationalization-discovery.v1", "classifications": []},
        "deletion_slices": [
            {
                "id": "test_slice",
                "state": state,
                "disposition": "DEAD_DELETE",
                "paths": [path],
                **({"merge_commit": merge_commit} if merge_commit else {}),
            }
        ],
    }
    support = {
        "schema": gate.EVIDENCE_SCHEMA,
        "slices": {
            "test_slice": {
                **({"base_sha": base} if state == "deleted_on_work_branch" else {}),
                "owner_issue": owner_issue,
                "path_evidence": [evidence or _path_evidence(finding_id=f"manual_candidate:{path}")],
            }
        },
    }
    ledger_path = repo / "ledger.json"
    evidence_path = repo / "evidence.json"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    evidence_path.write_text(json.dumps(support), encoding="utf-8")
    return ledger_path, evidence_path


def _validate(repo: Path, ledger: Path, evidence: Path):
    return gate.validate_repository(repo, ledger, evidence, REGISTRY)


def test_valid_work_branch_deletion_is_bound_to_exact_diff(tmp_path: Path) -> None:
    repo, base, head = _repo(tmp_path)
    ledger, evidence = _write_contract(repo, path="obsolete.py", base=base)

    result = _validate(repo, ledger, evidence)

    assert result["ok"] is True
    assert result["exact_head"] == head
    assert result["work_branch_deleted_path_count"] == 1


def test_valid_historical_merged_deletion_requires_reachable_commit_and_absence(tmp_path: Path) -> None:
    repo, base, head = _repo(tmp_path)
    ledger, evidence = _write_contract(
        repo,
        path="obsolete.py",
        base=base,
        state="accepted_and_merged",
        merge_commit=head,
    )

    result = _validate(repo, ledger, evidence)

    assert result["ok"] is True
    assert result["validated_path_count"] == 1


def test_missing_or_invalid_base_sha_fails_closed(tmp_path: Path) -> None:
    repo, base, _ = _repo(tmp_path)
    ledger, evidence = _write_contract(repo, path="obsolete.py", base=base)
    raw = json.loads(evidence.read_text())
    raw["slices"]["test_slice"]["base_sha"] = "not-a-sha"
    evidence.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(gate.DeletionAuthorizationError, match="deletion_base_sha_invalid"):
        _validate(repo, ledger, evidence)


def test_declared_path_absent_from_actual_deleted_diff_fails_closed(tmp_path: Path) -> None:
    repo, base, _ = _repo(tmp_path)
    (repo / "obsolete.py").write_text("obsolete = True\n", encoding="utf-8")
    _commit(repo, "restore original path so actual deletion set is empty")
    ledger, evidence = _write_contract(repo, path="fabricated.py", base=base)

    with pytest.raises(gate.DeletionAuthorizationError, match="declared_deleted_path_not_in_diff"):
        _validate(repo, ledger, evidence)


def test_actual_deleted_path_omitted_from_slice_fails_closed(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q", "-b", "main")
    obsolete = tmp_path / "obsolete.py"
    other = tmp_path / "other.py"
    obsolete.write_text("obsolete = True\n", encoding="utf-8")
    other.write_text("other = True\n", encoding="utf-8")
    base = _commit(tmp_path, "base with two paths")
    obsolete.unlink()
    other.unlink()
    _commit(tmp_path, "delete two paths")
    ledger, evidence = _write_contract(tmp_path, path="obsolete.py", base=base)

    with pytest.raises(gate.DeletionAuthorizationError, match="actual_deleted_path_omitted"):
        _validate(tmp_path, ledger, evidence)


def test_missing_required_path_evidence_fails_closed(tmp_path: Path) -> None:
    repo, base, _ = _repo(tmp_path)
    row = _path_evidence()
    row.pop("rollback_recovery")
    ledger, evidence = _write_contract(repo, path="obsolete.py", base=base, evidence=row)

    with pytest.raises(gate.DeletionAuthorizationError, match="deletion_path_evidence_fields_invalid"):
        _validate(repo, ledger, evidence)


def test_deleted_test_requires_explicit_migration_or_intentional_disposition(tmp_path: Path) -> None:
    path = "backend/tests/test_obsolete.py"
    repo, base, _ = _repo(tmp_path, path)
    row = _path_evidence(
        finding_id=f"manual_candidate:{path}",
        test_disposition="Coverage is gone and no further disposition exists.",
    )
    ledger, evidence = _write_contract(repo, path=path, base=base, evidence=row)

    with pytest.raises(gate.DeletionAuthorizationError, match="deleted_test_migration_disposition_missing"):
        _validate(repo, ledger, evidence)


def test_protected_domain_requires_owning_issue_authorization(tmp_path: Path) -> None:
    path = "frontend/app.js"
    repo, base, _ = _repo(tmp_path, path)
    ledger, evidence = _write_contract(repo, path=path, base=base, owner_issue=744)

    with pytest.raises(gate.DeletionAuthorizationError, match="protected_domain_owner_mismatch"):
        _validate(repo, ledger, evidence)


def test_duplicate_path_or_slice_id_fails_closed(tmp_path: Path) -> None:
    repo, base, _ = _repo(tmp_path)
    ledger, evidence = _write_contract(repo, path="obsolete.py", base=base)
    raw = json.loads(ledger.read_text())
    raw["deletion_slices"][0]["paths"].append("obsolete.py")
    ledger.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(gate.DeletionAuthorizationError, match="deletion_slice_path_duplicate"):
        _validate(repo, ledger, evidence)


def test_unreachable_merged_commit_and_reintroduced_path_fail_closed(tmp_path: Path) -> None:
    repo, base, head = _repo(tmp_path)
    ledger, evidence = _write_contract(
        repo,
        path="obsolete.py",
        base=base,
        state="accepted_and_merged",
        merge_commit="f" * 40,
    )
    with pytest.raises(gate.DeletionAuthorizationError, match="merged_deletion_commit_unreachable"):
        _validate(repo, ledger, evidence)

    raw = json.loads(ledger.read_text())
    raw["deletion_slices"][0]["merge_commit"] = head
    ledger.write_text(json.dumps(raw), encoding="utf-8")
    (repo / "obsolete.py").write_text("reintroduced = True\n", encoding="utf-8")
    _commit(repo, "reintroduce")
    with pytest.raises(gate.DeletionAuthorizationError, match="merged_deleted_path_reintroduced"):
        _validate(repo, ledger, evidence)


def test_current_repository_deletion_authorization_is_valid() -> None:
    result = gate.validate_repository(
        ROOT,
        ROOT / "docs/ai/codebase-rationalization-inventory.v1.yaml",
        ROOT / "config/governance/rationalization-deletion-evidence.v1.json",
        REGISTRY,
    )
    assert result["ok"] is True
    assert result["work_branch_deleted_path_count"] == 8
    assert result["validated_path_count"] == 9
