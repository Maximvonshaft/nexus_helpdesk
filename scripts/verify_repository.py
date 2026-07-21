#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
CANONICAL_WORKFLOW = ".github/workflows/canonical-acceptance.yml"
CURRENT_COMPATIBILITY_REGISTRY = "config/governance/legacy-surface-domains.v2.json"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
PINNED_ACTION = re.compile(r"^\s*-?\s*uses:\s*[^\s@]+@[0-9a-f]{40}(?:\s+#.*)?$")
EXACT_REQUIREMENT = re.compile(r"^[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?==[^\s;]+(?:\s*;.*)?$")

RETIRED_PATHS = (
    "frontend",
    "artifacts/supply-chain",
    "webapp/src/features/support-console",
    "webapp/src/shared/ui",
    "webapp/src/shared/api",
    "webapp/src/lib/api.ts",
    "webapp/src/lib/webchatRealtime.ts",
    "webapp/src/components/ui",
    "webapp/src/styles/tokens.css",
    "webapp/src/styles/components.css",
    "webapp/src/styles/auth.css",
    "webapp/src/app/app-shell.css",
    "webapp/src/features/operator-workspace/OperatorWorkspaceCommon.tsx",
    "webapp/src/features/operator-workspace/operator-workspace.css",
    "webapp/src/features/operator-workspace/operator-workspace-refinements.css",
    "webapp/src/features/admin-routes/admin-routes.css",
    "webapp/src/features/knowledge/knowledge.css",
    "webapp/src/features/knowledge/KnowledgeReadOnlyPage.tsx",
    "webapp/src/features/runtime/runtime-evidence-audit.css",
    "webapp/src/lib/cn.ts",
    "backend/app/services/outbound_adapters/whatsapp.py",
    "backend/app/services/webchat_ai_decision_runtime/prompt_builder.py",
    "backend/app/services/canonical_ticket_service.py",
    "backend/app/services/canonical_operator_work_queue.py",
    "backend/app/services/canonical_webchat_handoff_service.py",
    "backend/app/services/control_tower_service.py",
    "backend/app/services/qa_training_service.py",
    "backend/app/api/osr_admin.py",
    "backend/app/api/integration.py",
    "config/governance/legacy-surface-domains.v1.json",
    "deploy/docker-compose.server.yml",
    "deploy/docker-compose.candidate.yml",
    "deploy/.env.prod.example",
    "deploy/.env.prod.local-postgres.example",
    "deploy/.env.prod.external-postgres.example",
    "deploy/.env.candidate.example",
    "nexus-pr763-backend-source.tar.gz",
)

RETIRED_GOVERNANCE_PATHS = (
    "config/governance/actions-authority.v1.json",
    "config/governance/release-candidate-preconditions.v1.json",
    "scripts/ci/actions_authority_inventory.py",
    "scripts/release/exact_main_candidate_preconditions.py",
    "docs/superpowers/plans/2026-07-14-actions-authority-convergence.md",
)

RETIRED_HISTORY_GLOBS = (
    "ROUND*",
    "docs/round-*",
    "docs/round_*",
    "docs/history/rounds/**",
    "backend/scripts/smoke_verify_round*.py",
    "backend/scripts/round*.py",
    "scripts/smoke/*round*.sh",
    "backend/tests/test_round*.py",
    "backend/tests/test_pr27*.py",
    "scripts/validate_pr27_closure.sh",
)

REQUIRED_PATHS = (
    CANONICAL_WORKFLOW,
    "webapp/package-lock.json",
    "webapp/scripts/assert-frontend-architecture.mjs",
    "webapp/scripts/assert-http-transport-authority.mjs",
    "webapp/src/app/AppShell.tsx",
    "webapp/src/app/navigation.ts",
    "webapp/src/app/OperatorPresentation.tsx",
    "webapp/src/theme/nexusTheme.ts",
    "webapp/src/theme/NexusThemeProvider.tsx",
    "webapp/src/features/knowledge/KnowledgePage.tsx",
    "webapp/src/features/operator-workspace/OperatorWorkspacePage.tsx",
    "webapp/src/features/operator-workspace/OperatorWorkspaceClosure.tsx",
    "webapp/src/lib/apiClient.ts",
    "webapp/src/lib/ticketClosureTypes.ts",
    "backend/app/db.py",
    "backend/app/services/ticket_service.py",
    "backend/app/services/ticket_service_core.py",
    "backend/app/services/ticket_closure_readiness.py",
    "backend/app/api/ticket_closure.py",
    "backend/app/services/operator_work_queue.py",
    "backend/app/services/operator_work_queue_core.py",
    "backend/app/services/webchat_handoff_service.py",
    "backend/app/services/webchat_handoff_service_core.py",
    "backend/app/services/worker_progress.py",
    "backend/alembic/versions/20260720_0063_retire_legacy_channel_persistence.py",
    "backend/tests/test_retired_persistence_absence.py",
    "backend/scripts/run_worker.py",
    "backend/scripts/run_worker_supervised.py",
    "backend/scripts/check_worker_progress.py",
    "backend/app/services/permissions.py",
    "backend/app/services/provider_runtime/router.py",
    "backend/app/services/provider_runtime/traffic_selection.py",
    "backend/app/services/background_job_transaction_boundary.py",
    "backend/app/services/outbound_dispatch_transaction_boundary.py",
    "backend/app/services/queue_health.py",
    "backend/app/services/release_readiness.py",
    "backend/app/services/storage_readiness.py",
    "backend/app/api/canonical_integration.py",
    "backend/app/api/canonical_osr_admin.py",
    "backend/app/services/canonical_control_tower_service.py",
    "backend/app/services/canonical_qa_training_service.py",
    "config/architecture/service-authority.v1.json",
    "config/architecture/compatibility-lifecycle.v1.json",
    CURRENT_COMPATIBILITY_REGISTRY,
    "scripts/ci/check_legacy_surface_registry.py",
    "scripts/ci/check_agent_runtime_residue.py",
    "scripts/qualification/service_authority.py",
    "scripts/qualification/route_authority.py",
    "scripts/qualification/database_capacity.py",
    "scripts/qualification/supply_chain.py",
    "scripts/qualification/exact_head_acceptance.py",
    "scripts/qualification/postgres_acceptance.py",
    "scripts/release/assemble_supply_chain_evidence.py",
    "deploy/docker-compose.controlled.yml",
    "deploy/docker-compose.controlled-postgres.yml",
    "deploy/nexus-prod-compose.sh",
    "docs/ai/codebase-rationalization-inventory.v1.yaml",
)

FOCUSED_BACKEND_TESTS = (
    "backend/tests/test_canonical_service_authorities.py",
    "backend/tests/test_fastapi_route_authority.py",
    "backend/tests/test_ticket_safe_closure_contract.py",
    "backend/tests/test_worker_progress_health.py",
    "backend/tests/test_migration_0062_runtime_contract_provenance.py",
    "backend/tests/test_retired_persistence_absence.py",
    "backend/tests/test_migration_0063_legacy_channel_retirement.py",
    "backend/tests/test_controlled_least_privilege.py",
    "backend/tests/test_supply_chain_qualification.py",
)


class VerificationCommandError(RuntimeError):
    def __init__(self, stage: str, return_code: int) -> None:
        self.stage = stage
        self.return_code = return_code
        super().__init__(f"{stage} failed with return code {return_code}")


def _inside_repository(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return False
    return True


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.strip()


def _tracked_files() -> list[str]:
    return sorted(line for line in _git("ls-files").splitlines() if line.strip())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repository_identity() -> dict[str, Any]:
    status = _git("status", "--porcelain")
    return {
        "schema": "nexus.candidate-identity.v3",
        "source_sha": _git("rev-parse", "HEAD"),
        "tree_sha": _git("rev-parse", "HEAD^{tree}"),
        "clean": not bool(status),
        "dirty_paths": status.splitlines()[:50],
    }


def _load_json(relative: str, failures: list[str]) -> dict[str, Any] | None:
    try:
        value = json.loads((ROOT / relative).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        failures.append(f"invalid JSON authority {relative}: {type(exc).__name__}")
        return None
    if not isinstance(value, dict):
        failures.append(f"JSON authority must be an object: {relative}")
        return None
    return value


def _qualification_failures(relative: str) -> list[str]:
    completed = subprocess.run(
        [sys.executable, relative],
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode == 0:
        return []
    details = (completed.stdout or completed.stderr).strip()
    try:
        payload = json.loads(completed.stdout)
        findings = payload.get("findings") or payload.get("duplicates") or []
        if findings:
            return [f"{relative}: {item}" for item in findings]
    except (json.JSONDecodeError, AttributeError):
        pass
    return [f"qualification failed {relative}: {details[:2000]}"]


def _workflow_failures() -> list[str]:
    files = (
        sorted(
            path.relative_to(ROOT).as_posix()
            for path in WORKFLOW_DIR.rglob("*")
            if path.is_file()
        )
        if WORKFLOW_DIR.is_dir()
        else []
    )
    if files != [CANONICAL_WORKFLOW]:
        return [
            "exactly one canonical GitHub Actions workflow is required: "
            f"expected={[CANONICAL_WORKFLOW]} actual={files}"
        ]
    failures: list[str] = []
    content = (ROOT / CANONICAL_WORKFLOW).read_text(encoding="utf-8")
    for marker in (
        "name: Canonical Acceptance",
        "pull_request:",
        "workflow_dispatch:",
        "contents: read",
        "required-gate:",
        "python scripts/verify_repository.py --static-only",
        "python scripts/qualification/postgres_acceptance.py",
        "npm run verify",
        "npm run e2e",
        "docker build",
    ):
        if marker not in content:
            failures.append(f"canonical workflow marker missing: {marker}")
    for forbidden in (
        "pull_request_target:",
        "paths-ignore:",
        "secrets.",
        "continue-on-error: true",
    ):
        if forbidden in content:
            failures.append(f"canonical workflow contains forbidden marker: {forbidden}")
    for line in (
        line for line in content.splitlines() if re.match(r"^\s*-?\s*uses:", line)
    ):
        if not PINNED_ACTION.fullmatch(line):
            failures.append(f"workflow action is not pinned to a full SHA: {line.strip()}")
    job_count = len(re.findall(r"^  [a-zA-Z0-9_-]+:\s*$", content, re.MULTILINE))
    if job_count == 0 or content.count("timeout-minutes:") < job_count:
        failures.append("every workflow job must set timeout-minutes")
    return failures


def _compatibility_failures() -> list[str]:
    failures: list[str] = []
    payload = _load_json("config/architecture/compatibility-lifecycle.v1.json", failures)
    if payload is None:
        return failures
    if payload.get("schema") != "nexus.compatibility-lifecycle.v1":
        failures.append("compatibility lifecycle schema is invalid")
    if payload.get("detailed_registry") != CURRENT_COMPATIBILITY_REGISTRY:
        failures.append("compatibility lifecycle does not point to the current registry")
    if payload.get("enforcement_entrypoint") != "scripts/ci/check_legacy_surface_registry.py":
        failures.append("compatibility lifecycle enforcement entrypoint is not canonical")
    assets = payload.get("assets")
    if not isinstance(assets, list) or not assets:
        return [*failures, "compatibility lifecycle assets are missing"]
    seen: set[str] = set()
    today = date.today()
    for item in assets:
        if not isinstance(item, dict):
            failures.append("compatibility lifecycle item is invalid")
            continue
        relative = str(item.get("path") or "")
        if not relative or relative in seen:
            failures.append(f"compatibility lifecycle path missing or duplicate: {relative}")
            continue
        seen.add(relative)
        if not (ROOT / relative).exists():
            failures.append(f"compatibility lifecycle path missing: {relative}")
        if not item.get("owner"):
            failures.append(f"compatibility lifecycle owner missing: {relative}")
        remove_after = item.get("remove_after")
        if remove_after:
            try:
                deadline = date.fromisoformat(str(remove_after))
            except ValueError:
                failures.append(f"compatibility lifecycle deadline invalid: {relative}")
            else:
                if deadline <= today:
                    failures.append(
                        f"compatibility lifecycle deadline expired: {relative}:{deadline.isoformat()}"
                    )
    return failures


def _path_failures() -> list[str]:
    failures: list[str] = []
    for relative in RETIRED_PATHS:
        if (ROOT / relative).exists():
            failures.append(f"retired path exists: {relative}")
    for relative in RETIRED_GOVERNANCE_PATHS:
        if (ROOT / relative).exists():
            failures.append(f"retired governance residue exists: {relative}")
    for relative in REQUIRED_PATHS:
        if not (ROOT / relative).is_file():
            failures.append(f"canonical authority missing: {relative}")
    for path in _tracked_files():
        if path.startswith("backend/alembic/versions/"):
            continue
        if any(fnmatch.fnmatchcase(path.casefold(), pattern.casefold()) for pattern in RETIRED_HISTORY_GLOBS):
            failures.append(f"historical delivery residue exists: {path}")
    return failures


def _migration_authority_failures() -> list[str]:
    failures: list[str] = []
    tracked_sql = [line for line in _git("ls-files", "*.sql").splitlines() if line.strip()]
    if tracked_sql:
        failures.append(
            "Alembic is the only schema mutation authority; tracked raw SQL exists: "
            f"{tracked_sql}"
        )
    ddl_pattern = re.compile(r"\b(?:CREATE|ALTER|DROP)\s+TABLE\b", re.IGNORECASE)
    ddl_offenders = [
        path.relative_to(ROOT).as_posix()
        for path in sorted((ROOT / "backend/app").rglob("*.py"))
        if ddl_pattern.search(path.read_text(encoding="utf-8"))
    ]
    if ddl_offenders:
        failures.append(f"runtime DDL exists outside Alembic: {ddl_offenders}")
    migration = ROOT / "backend/alembic/versions/20260716_0062_canonical_runtime_contracts.py"
    if migration.is_file():
        source = migration.read_text(encoding="utf-8")
        for marker in (
            "migration_0062_runtime_contract_rows",
            "downgrade_provenance_missing",
            "downgrade_conflict",
            "sa.String(length=36)",
        ):
            if marker not in source:
                failures.append(f"migration 0062 provenance marker missing: {marker}")

    retirement = ROOT / "backend/alembic/versions/20260720_0063_retire_legacy_channel_persistence.py"
    if retirement.is_file():
        source = retirement.read_text(encoding="utf-8")
        for marker in (
            "migration_retirement_archive",
            "canonical_refs_json",
            "retirement_archive_payload_hash_mismatch",
            "retirement_canonical_row_missing",
            "_delete_canonical_refs",
        ):
            if marker not in source:
                failures.append(f"migration 0063 retirement marker missing: {marker}")
    return failures


def _retired_persistence_failures() -> list[str]:
    """Reject reintroduction outside protected schema history and its proof tests."""

    failures: list[str] = []
    snake = "external" + "_channel"
    pascal = "External" + "Channel"
    kebab = "external" + "-channel"
    markers = (snake, pascal, kebab)
    allowed = {
        "backend/tests/test_migration_0063_legacy_channel_retirement.py",
        "backend/tests/test_retired_persistence_absence.py",
    }
    text_suffixes = {".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".yaml", ".yml", ".md", ".sh"}
    for relative in _tracked_files():
        if relative.startswith("backend/alembic/versions/") or relative in allowed:
            continue
        path = ROOT / relative
        if path.suffix.lower() not in text_suffixes or not path.is_file():
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if any(marker in source or marker in relative for marker in markers):
            failures.append(f"retired channel persistence residue exists: {relative}")
    return failures


def _frontend_failures() -> list[str]:
    failures: list[str] = []
    package_path = ROOT / "webapp/package.json"
    if not package_path.is_file():
        return failures
    package = json.loads(package_path.read_text(encoding="utf-8"))
    scripts = package.get("scripts") or {}
    if "assert-http-transport-authority.mjs" not in str(scripts.get("architecture") or ""):
        failures.append("frontend architecture command omits transport authority")
    if "npm run architecture" not in str(scripts.get("verify") or ""):
        failures.append("frontend verify command bypasses architecture authority")
    for section in ("dependencies", "devDependencies", "overrides"):
        for name, version in (package.get(section) or {}).items():
            if not re.fullmatch(
                r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?",
                str(version),
            ):
                failures.append(
                    f"frontend direct dependency is not exact: {section}:{name}:{version}"
                )
    return failures


def _worker_and_closure_failures() -> list[str]:
    failures: list[str] = []
    compose = (ROOT / "deploy/docker-compose.controlled.yml").read_text(encoding="utf-8")
    for marker in (
        "run_worker_supervised.py",
        "scripts/check_worker_progress.py",
        "NEXUS_WORKER_ID",
        "NEXUS_WORKER_QUEUE",
    ):
        if marker not in compose:
            failures.append(f"controlled worker progress marker missing: {marker}")
    for forbidden in ("/proc/1/cmdline", "controlled-worker-ok", "--queue all"):
        if forbidden in compose:
            failures.append(f"retired worker health/runtime marker returned: {forbidden}")

    closure = (ROOT / "backend/app/services/ticket_service.py").read_text(encoding="utf-8")
    for marker in (
        "require_closure_ready",
        "append_closure_receipt_event",
        "invalidate_latest_closure_receipt",
    ):
        if marker not in closure:
            failures.append(f"safe closure authority marker missing: {marker}")
    return failures


def _supply_input_failures() -> list[str]:
    failures: list[str] = []
    dockerfile = ROOT / "Dockerfile"
    effective = "\n".join(
        line
        for line in dockerfile.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )
    for line in effective.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("FROM ") and "@sha256:" not in stripped.split()[1]:
            failures.append(f"Dockerfile contains an unpinned base image: {stripped}")
    if re.search(r"\bapk\s+upgrade\b", effective):
        failures.append("Dockerfile reintroduced mutable apk upgrade")
    for number, line in enumerate(
        (ROOT / "backend/requirements.txt").read_text(encoding="utf-8").splitlines(),
        1,
    ):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("--"):
            continue
        if not EXACT_REQUIREMENT.fullmatch(stripped):
            failures.append(f"Python requirement is not exact at line {number}: {stripped[:120]}")
    return failures


def _governance_truth_failures() -> list[str]:
    failures: list[str] = []
    path = ROOT / "docs/ai/codebase-rationalization-inventory.v1.yaml"
    source = path.read_text(encoding="utf-8")
    for stale_key in (
        "canonical_pr:",
        "canonical_branch:",
        "baseline_main_sha:",
        "exact_head_status:",
        "candidate_in_progress",
    ):
        if stale_key in source:
            failures.append(f"mutable delivery status returned to authority inventory: {stale_key}")
    return failures


def static_failures() -> list[str]:
    failures: list[str] = []
    failures.extend(_workflow_failures())
    failures.extend(_path_failures())
    failures.extend(_compatibility_failures())
    failures.extend(_qualification_failures("scripts/qualification/service_authority.py"))
    failures.extend(_qualification_failures("scripts/ci/check_legacy_surface_registry.py"))
    failures.extend(_qualification_failures("scripts/ci/check_agent_runtime_residue.py"))
    failures.extend(_migration_authority_failures())
    failures.extend(_retired_persistence_failures())
    failures.extend(_frontend_failures())
    failures.extend(_worker_and_closure_failures())
    failures.extend(_supply_input_failures())
    failures.extend(_governance_truth_failures())
    return failures


def run(
    stage: str,
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    display: list[str] | None = None,
) -> None:
    print(f"+ {' '.join(display or command)}", flush=True)
    completed = subprocess.run(command, cwd=cwd or ROOT, env=env, check=False)
    if completed.returncode != 0:
        raise VerificationCommandError(stage, completed.returncode)


def _write_evidence(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    resolved = path.expanduser().resolve()
    if _inside_repository(resolved):
        raise ValueError("verification evidence output must remain outside candidate tree")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _prepare_external_directory(path: Path, label: str) -> Path:
    directory = path.expanduser().resolve()
    if _inside_repository(directory) or directory.is_symlink():
        raise ValueError(f"{label} must be outside candidate tree")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _argument_failures(args: argparse.Namespace) -> list[str]:
    failures: list[str] = []
    if args.expected_sha and not SHA40.fullmatch(args.expected_sha):
        failures.append("expected SHA must be exactly 40 lowercase hex characters")
    if args.acceptance_evidence_dir is not None:
        if args.static_only or args.focused_backend or args.skip_browser:
            failures.append("final acceptance requires the complete verification profile")
        if not args.expected_sha:
            failures.append("final acceptance requires --expected-sha")
        if args.release_evidence_dir is None:
            failures.append("final acceptance requires --release-evidence-dir")
        if not args.acceptance_database_url:
            failures.append("final acceptance requires --acceptance-database-url or DATABASE_URL")
        if args.acceptance_upload_source is None or args.acceptance_upload_backup is None:
            failures.append("final acceptance requires upload source and backup paths")
    return failures


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the single canonical Nexus implementation on one exact commit."
    )
    parser.add_argument("--static-only", action="store_true")
    parser.add_argument("--skip-browser", action="store_true")
    parser.add_argument("--focused-backend", action="store_true")
    parser.add_argument("--release-evidence-dir", type=Path)
    parser.add_argument("--evidence-out", type=Path)
    parser.add_argument("--expected-sha")
    parser.add_argument("--acceptance-evidence-dir", type=Path)
    parser.add_argument("--acceptance-database-url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument("--acceptance-upload-source", type=Path)
    parser.add_argument("--acceptance-upload-backup", type=Path)
    parser.add_argument("--acceptance-manifest-name", default="acceptance-manifest.json")
    parser.add_argument("--allow-remote-acceptance-database", action="store_true")
    args = parser.parse_args(argv)

    started_at = datetime.now(timezone.utc).isoformat()
    argument_failures = _argument_failures(args)
    if argument_failures:
        payload = {
            "schema": "nexus.canonical-verification.v3",
            "status": "fail",
            "stage": "arguments",
            "started_at": started_at,
            "failures": argument_failures,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        _write_evidence(args.evidence_out, payload)
        return 1

    try:
        start = repository_identity()
    except (OSError, subprocess.CalledProcessError) as exc:
        payload = {
            "schema": "nexus.canonical-verification.v3",
            "status": "fail",
            "stage": "candidate_identity",
            "started_at": started_at,
            "error_type": type(exc).__name__,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        _write_evidence(args.evidence_out, payload)
        return 1

    failures = static_failures()
    if not start["clean"]:
        failures.append("candidate working tree is not clean before verification")
    if args.expected_sha and start["source_sha"] != args.expected_sha:
        failures.append(
            f"candidate source SHA mismatch: expected={args.expected_sha} actual={start['source_sha']}"
        )
    static_payload = {"static_ok": not failures, "failures": failures, "candidate": start}
    print(json.dumps(static_payload, ensure_ascii=False, indent=2))
    if failures:
        payload = {
            "schema": "nexus.canonical-verification.v3",
            "status": "fail",
            "stage": "static",
            "started_at": started_at,
            **static_payload,
        }
        _write_evidence(args.evidence_out, payload)
        return 1

    acceptance_result: str | None = None
    try:
        supply_chain = [sys.executable, "scripts/qualification/supply_chain.py"]
        if args.release_evidence_dir:
            supply_chain.extend(
                ["--release", "--evidence-dir", str(args.release_evidence_dir.resolve())]
            )
        run("supply_chain", supply_chain)
        if not args.static_only:
            run("service_authority", [sys.executable, "scripts/qualification/service_authority.py"])
            run("route_authority", [sys.executable, "scripts/qualification/route_authority.py"])
            run("alembic_heads", [sys.executable, "-m", "alembic", "heads"], cwd=ROOT / "backend")
            run(
                "frontend_install",
                ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
                cwd=ROOT / "webapp",
            )
            run("frontend_verify", ["npm", "run", "verify"], cwd=ROOT / "webapp")
            run(
                "python_compile",
                [
                    sys.executable,
                    "-m",
                    "compileall",
                    "backend/app",
                    "backend/scripts",
                    "scripts/qualification",
                    "scripts/release",
                ],
            )
            backend_tests = list(FOCUSED_BACKEND_TESTS) if args.focused_backend else ["backend/tests"]
            run("backend_tests", [sys.executable, "-m", "pytest", "-q", *backend_tests])
            if not args.skip_browser:
                run("browser_tests", ["npm", "run", "e2e"], cwd=ROOT / "webapp")

        if args.acceptance_evidence_dir is not None:
            acceptance_dir = _prepare_external_directory(
                args.acceptance_evidence_dir,
                "acceptance evidence directory",
            )
            postgres_env = os.environ.copy()
            postgres_env["DATABASE_URL"] = args.acceptance_database_url
            postgres_command = [
                sys.executable,
                "scripts/qualification/postgres_acceptance.py",
                "--evidence-dir",
                str(acceptance_dir),
                "--source-sha",
                start["source_sha"],
                "--tree-sha",
                start["tree_sha"],
                "--output",
                str(acceptance_dir / "postgres-acceptance-run.json"),
            ]
            if args.allow_remote_acceptance_database:
                postgres_command.append("--allow-remote-database")
            run(
                "postgres_acceptance",
                postgres_command,
                env=postgres_env,
                display=[
                    sys.executable,
                    "scripts/qualification/postgres_acceptance.py",
                    "--database-url",
                    "<redacted-disposable-postgresql-url>",
                    "--evidence-dir",
                    str(acceptance_dir),
                ],
            )
            run(
                "exact_head_acceptance",
                [
                    sys.executable,
                    "scripts/qualification/exact_head_acceptance.py",
                    "--evidence-dir",
                    str(acceptance_dir),
                    "--source-sha",
                    start["source_sha"],
                    "--tree-sha",
                    start["tree_sha"],
                    "--manifest-name",
                    args.acceptance_manifest_name,
                    "--output",
                    str(acceptance_dir / "exact-head-acceptance.json"),
                ],
            )
            acceptance_result = str(acceptance_dir / "exact-head-acceptance.json")
    except VerificationCommandError as exc:
        payload = {
            "schema": "nexus.canonical-verification.v3",
            "status": "fail",
            "stage": exc.stage,
            "return_code": exc.return_code,
            "started_at": started_at,
            "candidate": start,
        }
        _write_evidence(args.evidence_out, payload)
        return 1

    end = repository_identity()
    immutable = start == end and end["clean"]
    payload = {
        "schema": "nexus.canonical-verification.v3",
        "status": "pass" if immutable else "fail",
        "stage": "complete" if immutable else "candidate_identity_changed",
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "candidate": end,
        "source_sha_unchanged": start["source_sha"] == end["source_sha"],
        "tree_sha_unchanged": start["tree_sha"] == end["tree_sha"],
        "acceptance_result": acceptance_result,
        "production_authorized": False,
        "provider_enablement_authorized": False,
        "outbound_enablement_authorized": False,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    _write_evidence(args.evidence_out, payload)
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
