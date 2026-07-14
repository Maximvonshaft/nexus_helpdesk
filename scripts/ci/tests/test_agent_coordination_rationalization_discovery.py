from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts.ci import rationalization_discovery as discovery


def _git_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    for path, content in files.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    return tmp_path


def test_root_and_suspicious_name_detection() -> None:
    tracked = ["README.md", "FINAL_REPORT.md", "check_bridge.py", "src/foo_backup.py"]
    root = discovery._root_findings(tracked)
    names = discovery._suspicious_name_findings(tracked)
    assert {row["finding_id"] for row in root} == {
        "root_document:FINAL_REPORT.md",
        "root_executable:check_bridge.py",
    }
    assert {row["finding_id"] for row in names} == {"suspicious_name:src/foo_backup.py"}


def test_source_reads_fail_closed_when_oversized(tmp_path: Path) -> None:
    target = tmp_path / "large.py"
    target.write_bytes(b"x" * (discovery.MAX_TEXT_BYTES + 1))

    with pytest.raises(discovery.DiscoveryError, match=r"source_unavailable:large\.py"):
        discovery._read_source(tmp_path, "large.py")


def test_duplicate_detection_uses_content_hash(tmp_path: Path) -> None:
    repo = _git_repo(
        tmp_path,
        {
            "a.py": "def shared():\n    return 'same payload with enough content to exceed the duplicate threshold'\n",
            "b.py": "def shared():\n    return 'same payload with enough content to exceed the duplicate threshold'\n",
            "short.py": "x=1\n",
        },
    )
    findings = discovery._duplicate_findings(repo, discovery.collect_tracked_files(repo))
    assert len(findings) == 1
    assert findings[0]["paths"] == ["a.py", "b.py"]
    assert findings[0]["finding_id"].startswith("exact_duplicate_text:")


def test_frontend_reachability_supports_relative_and_alias_imports(tmp_path: Path) -> None:
    repo = _git_repo(
        tmp_path,
        {
            "webapp/src/main.tsx": "import App from './App'\nimport '@/styles'\nvoid App\n",
            "webapp/src/App.tsx": "export { Widget } from './Widget'\n",
            "webapp/src/Widget.tsx": "export const Widget = 1\n",
            "webapp/src/styles.ts": "export const style = 1\n",
            "webapp/src/Orphan.tsx": "export const Orphan = 1\n",
        },
    )
    findings = discovery._frontend_findings(repo, discovery.collect_tracked_files(repo))
    assert [row["finding_id"] for row in findings] == [
        "unreachable_webapp_module:webapp/src/Orphan.tsx"
    ]


def test_backend_reachability_follows_package_reexports(tmp_path: Path) -> None:
    repo = _git_repo(
        tmp_path,
        {
            "backend/app/__init__.py": "",
            "backend/app/main.py": "from app.feature import run\nrun()\n",
            "backend/app/feature/__init__.py": "from .worker import run\n",
            "backend/app/feature/worker.py": "def run():\n    return 1\n",
        },
    )
    assert discovery._backend_findings(repo, discovery.collect_tracked_files(repo)) == []


def test_backend_reachability_uses_app_and_script_entrypoints(tmp_path: Path) -> None:
    repo = _git_repo(
        tmp_path,
        {
            "backend/app/__init__.py": "",
            "backend/app/main.py": "from app.service import run\nrun()\n",
            "backend/app/service.py": "def run():\n    return 1\n",
            "backend/app/worker_only.py": "def work():\n    return 2\n",
            "backend/app/orphan.py": "def unused():\n    return 3\n",
            "backend/scripts/run_worker.py": "from app.worker_only import work\nwork()\n",
        },
    )
    findings = discovery._backend_findings(repo, discovery.collect_tracked_files(repo))
    assert [row["finding_id"] for row in findings] == [
        "unreachable_backend_module:backend/app/orphan.py"
    ]


def test_ledger_classifications_are_strict(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.yaml"
    ledger.write_text(
        yaml.safe_dump(
            {
                "schema": discovery.LEDGER_SCHEMA,
                "discovery_gate": {
                    "contract": discovery.RESULT_SCHEMA,
                    "classifications": [
                        {
                            "finding_id": "root_document:report.md",
                            "disposition": "UNKNOWN_BLOCK_DELETE",
                            "owner_issue": 744,
                            "rationale": "This candidate needs bounded runtime and history evidence.",
                            "next_action": "Trace every consumer before deletion.",
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    result = discovery._load_ledger_classifications(ledger)
    assert result["root_document:report.md"]["owner_issue"] == 744


def test_ledger_rejects_duplicate_mapping_keys(tmp_path: Path) -> None:
    ledger = tmp_path / "duplicate.yaml"
    ledger.write_text(
        f"schema: {discovery.LEDGER_SCHEMA}\nschema: {discovery.LEDGER_SCHEMA}\n",
        encoding="utf-8",
    )

    with pytest.raises(discovery.DiscoveryError, match="ledger_duplicate_key"):
        discovery._load_ledger_classifications(ledger)


def test_ledger_rejects_yaml_indirection(tmp_path: Path) -> None:
    ledger = tmp_path / "alias.yaml"
    ledger.write_text(
        (
            f"schema: {discovery.LEDGER_SCHEMA}\n"
            "discovery_gate:\n"
            f"  contract: {discovery.RESULT_SCHEMA}\n"
            "  classifications: &rows []\n"
            "copy: *rows\n"
        ),
        encoding="utf-8",
    )

    with pytest.raises(discovery.DiscoveryError, match="ledger_yaml_indirection_forbidden"):
        discovery._load_ledger_classifications(ledger)


def test_ledger_rejects_oversized_input(tmp_path: Path) -> None:
    ledger = tmp_path / "oversized.yaml"
    ledger.write_bytes(b"x" * (discovery.MAX_LEDGER_BYTES + 1))

    with pytest.raises(discovery.DiscoveryError, match="ledger_size_or_binary_invalid"):
        discovery._load_ledger_classifications(ledger)


def test_repository_ledger_consumes_the_canonical_console_manifest() -> None:
    root = Path(__file__).resolve().parents[3]
    ledger = discovery._load_ledger_document(
        root / "docs/ai/codebase-rationalization-inventory.v1.yaml"
    )
    authority = ledger["authority"]

    assert authority["canonical_console_pr"] == 748
    assert authority["canonical_console_manifest"] == (
        "webapp/design/operator-console-consolidation.v1.json"
    )
    assert "single repository rationalization authority" in authority["rule"]


def test_repository_discovery_is_fully_classified() -> None:
    root = Path(__file__).resolve().parents[3]
    result = discovery.scan_repository(
        root,
        root / "config/governance/legacy-surface-domains.v1.json",
        root / "docs/ai/codebase-rationalization-inventory.v1.yaml",
    )
    assert result["ok"], json.dumps(result, indent=2, sort_keys=True)
