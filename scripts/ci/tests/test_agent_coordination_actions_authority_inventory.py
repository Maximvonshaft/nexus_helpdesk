from __future__ import annotations

import json
from pathlib import Path

from scripts.ci import actions_authority_inventory as audit


ROOT = Path(__file__).resolve().parents[3]


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _safe_workflow(name: str) -> str:
    return f"""name: {name}
on: [workflow_dispatch]
permissions: {{}}
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo {name}
"""


def _inventory(tmp_path: Path, *, overrides: dict[str, dict[str, str]] | None = None) -> Path:
    workflows = tmp_path / ".github/workflows"
    authoritative = {
        "frontend": ".github/workflows/frontend-authority.yml",
        "backend": ".github/workflows/backend-authority.yml",
        "migration": ".github/workflows/migration-authority.yml",
        "security": ".github/workflows/security-authority.yml",
        "release": ".github/workflows/release-authority.yml",
        "governance": ".github/workflows/governance-authority.yml",
    }
    for authority, path_value in authoritative.items():
        _write(tmp_path / path_value, _safe_workflow(authority))
    return _write(
        tmp_path / "config/governance/actions-authority.v1.json",
        json.dumps(
            {
                "schema": audit.INVENTORY_SCHEMA,
                "authoritative": authoritative,
                "publication_allowlist": [],
                "historical_delete": [],
                "classification_overrides": overrides or {},
            },
            sort_keys=True,
        ),
    )


def test_mutable_action_reference_fails_closed(tmp_path: Path) -> None:
    workflow = _write(
        tmp_path / ".github/workflows/frontend.yml",
        """name: frontend
on: [pull_request]
permissions:
  contents: read
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
""",
    )
    findings = audit.audit_workflow(workflow, classification="authoritative", authority="frontend")
    assert "mutable_action_reference" in {row["code"] for row in findings}


def test_pull_request_write_permission_and_auto_commit_fail_closed(tmp_path: Path) -> None:
    workflow = _write(
        tmp_path / ".github/workflows/unsafe.yml",
        """name: unsafe
on: [pull_request]
permissions:
  contents: write
jobs:
  mutate:
    runs-on: ubuntu-latest
    steps:
      - run: |
          git commit -am update
          git push
""",
    )
    findings = audit.audit_workflow(workflow, classification="authoritative", authority="governance")
    codes = {row["code"] for row in findings}
    assert "pull_request_write_permission" in codes
    assert "pull_request_repository_mutation" in codes
    assert "contents_write_outside_publication" in codes


def test_contents_write_is_limited_to_release_publication(tmp_path: Path) -> None:
    workflow = _write(
        tmp_path / ".github/workflows/publish.yml",
        """name: publish
on: [workflow_dispatch]
permissions:
  contents: write
jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - run: echo publish
""",
    )
    assert audit.audit_workflow(workflow, classification="publication", authority="release") == []
    findings = audit.audit_workflow(workflow, classification="authoritative", authority="backend")
    assert "contents_write_outside_publication" in {row["code"] for row in findings}


def test_privileged_trigger_and_event_shell_injection_fail_closed(tmp_path: Path) -> None:
    workflow = _write(
        tmp_path / ".github/workflows/privileged.yml",
        """name: privileged
on: [pull_request_target]
permissions:
  contents: read
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5
        with:
          ref: ${{ github.event.pull_request.head.sha }}
          persist-credentials: false
      - run: echo "${{ github.event.pull_request.title }}"
""",
    )
    codes = {
        row["code"]
        for row in audit.audit_workflow(workflow, classification="matrix_component", authority="governance")
    }
    assert "privileged_trigger_executes_untrusted_head" in codes
    assert "untrusted_event_shell_interpolation" in codes


def test_duplicate_frontend_install_build_chains_are_rejected(tmp_path: Path) -> None:
    shared = """on: [pull_request]
permissions:
  contents: read
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: npm ci
      - run: npm test
      - run: npm run build
"""
    _write(tmp_path / ".github/workflows/one.yml", "name: one\n" + shared)
    _write(tmp_path / ".github/workflows/two.yml", "name: two\n" + shared)
    inventory = _inventory(
        tmp_path,
        overrides={
            ".github/workflows/one.yml": {"classification": "matrix_component", "authority": "frontend"},
            ".github/workflows/two.yml": {"classification": "matrix_component", "authority": "frontend"},
        },
    )

    result = audit.audit_repository(tmp_path, inventory)

    assert not result["ok"]
    assert "duplicate_frontend_install_build_authority" in result["failure_codes"]


def test_stale_inventory_path_fails_closed(tmp_path: Path) -> None:
    inventory = _inventory(
        tmp_path,
        overrides={
            ".github/workflows/missing.yml": {"classification": "matrix_component", "authority": "backend"}
        },
    )

    result = audit.audit_repository(tmp_path, inventory)

    assert "inventory_path_not_tracked" in result["failure_codes"]


def test_repository_actions_authority_is_converged() -> None:
    result = audit.audit_repository(
        ROOT,
        ROOT / "config/governance/actions-authority.v1.json",
    )
    assert result["ok"], json.dumps(result, indent=2, sort_keys=True)
    assert result["authority_counts"] == {
        "frontend": 1,
        "backend": 1,
        "migration": 1,
        "security": 1,
        "release": 1,
        "governance": 1,
    }
    assert ".github/workflows/generate-radix-lockfile.yml" not in result["tracked_workflows"]
