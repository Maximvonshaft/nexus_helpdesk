#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
CANONICAL_WORKFLOW = ".github/workflows/canonical-acceptance.yml"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
PINNED_ACTION = re.compile(r"^\s*-?\s*uses:\s*[^\s@]+@[0-9a-f]{40}(?:\s+#.*)?$")

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
)

ACTIONS_RESIDUE = (
    "config/governance/actions-authority.v1.json",
    "config/governance/release-candidate-preconditions.v1.json",
    "scripts/ci/actions_authority_inventory.py",
    "scripts/release/exact_main_candidate_preconditions.py",
    "docs/superpowers/plans/2026-07-14-actions-authority-convergence.md",
)

REQUIRED_CANONICAL_PATHS = (
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
    "webapp/src/features/operator-workspace/OperatorWorkspaceActions.tsx",
    "webapp/src/features/operator-workspace/OperatorWorkspaceQueue.tsx",
    "webapp/src/features/operator-workspace/OperatorWorkspaceCase.tsx",
    "webapp/src/features/operator-workspace/OperatorWorkspaceConversation.tsx",
    "webapp/src/lib/apiClient.ts",
    "webapp/src/domain/operationalPresentation.ts",
    "backend/app/db.py",
    "backend/app/services/permissions.py",
    "backend/app/services/support_sensitive_access.py",
    "backend/app/services/provider_runtime/router.py",
    "backend/app/services/provider_runtime/traffic_selection.py",
    "backend/app/services/background_job_transaction_boundary.py",
    "backend/app/services/outbound_dispatch_transaction_boundary.py",
    "backend/app/services/queue_health.py",
    "backend/app/services/release_readiness.py",
    "backend/app/services/storage_readiness.py",
    "backend/tests/test_canonical_service_authorities.py",
    "backend/tests/test_fastapi_route_authority.py",
    "backend/tests/test_exact_head_acceptance.py",
    "config/architecture/service-authority.v1.json",
    "config/architecture/compatibility-lifecycle.v1.json",
    "scripts/qualification/service_authority.py",
    "scripts/qualification/route_authority.py",
    "scripts/qualification/database_capacity.py",
    "scripts/qualification/infrastructure_decision.py",
    "scripts/qualification/local_storage_backup.py",
    "scripts/qualification/supply_chain.py",
    "scripts/qualification/exact_head_acceptance.py",
    "scripts/qualification/postgres_acceptance.py",
    "scripts/release/assemble_supply_chain_evidence.py",
    "docs/history/migrations/20260505-webchat-ai-turn-runtime.md",
    "docs/ops/EXACT_HEAD_ACCEPTANCE_RUNBOOK.md",
    "deploy/docker-compose.controlled.yml",
    "deploy/docker-compose.controlled-postgres.yml",
)

PUBLIC_COMPATIBILITY = {
    "backend/app/services/ticket_service.py": "canonical_ticket_service",
    "backend/app/services/control_tower_service.py": "canonical_control_tower_service",
    "backend/app/services/qa_training_service.py": "canonical_qa_training_service",
    "backend/app/services/operator_work_queue.py": "canonical_operator_work_queue",
    "backend/app/services/webchat_handoff_service.py": "canonical_webchat_handoff_service",
    "backend/app/api/osr_admin.py": "canonical_osr_admin",
    "backend/app/api/integration.py": "canonical_integration",
}

IDENTITY_FILES = (
    "backend/requirements.txt",
    "webapp/package.json",
    "webapp/package-lock.json",
    "Dockerfile",
    CANONICAL_WORKFLOW,
    "deploy/docker-compose.server.yml",
    "deploy/docker-compose.candidate.yml",
    "deploy/docker-compose.controlled.yml",
    "deploy/docker-compose.controlled-postgres.yml",
    "config/architecture/service-authority.v1.json",
    "config/architecture/compatibility-lifecycle.v1.json",
    "scripts/verify_repository.py",
    "scripts/qualification/service_authority.py",
    "scripts/qualification/route_authority.py",
    "scripts/qualification/database_capacity.py",
    "scripts/qualification/infrastructure_decision.py",
    "scripts/qualification/local_storage_backup.py",
    "scripts/qualification/supply_chain.py",
    "scripts/qualification/exact_head_acceptance.py",
    "scripts/qualification/postgres_acceptance.py",
    "scripts/release/assemble_supply_chain_evidence.py",
    "docs/ops/EXACT_HEAD_ACCEPTANCE_RUNBOOK.md",
)

FOCUSED_BACKEND_TESTS = (
    "backend/tests/test_canonical_service_authorities.py",
    "backend/tests/test_fastapi_route_authority.py",
    "backend/tests/test_exact_head_acceptance.py",
    "backend/tests/test_canonical_control_tower_authority.py",
    "backend/tests/test_runtime_permission_projection.py",
    "backend/tests/test_scope_permissions.py",
    "backend/tests/test_canonical_route_projection.py",
    "backend/tests/test_canonical_policy_projection_behavior.py",
    "backend/tests/test_canonical_policy_projection_contract.py",
    "backend/tests/test_operator_queue_current_scopes.py",
    "backend/tests/test_webchat_country_authority.py",
    "backend/tests/test_webchat_country_migration_contract.py",
    "backend/tests/test_webchat_public_tenant_binding.py",
    "backend/tests/test_channel_control.py",
    "backend/tests/test_knowledge_items.py",
    "backend/tests/test_outbound_semantics_single_source.py",
    "backend/tests/test_webchat_tracking_fact_mvp.py",
    "backend/tests/test_support_conversation_authority_contract.py",
    "backend/tests/test_support_conversation_privacy.py",
    "backend/tests/test_support_conversations_api.py",
    "backend/tests/test_support_conversations_rbac.py",
    "backend/tests/test_support_sensitive_access.py",
    "backend/tests/test_provider_runtime_traffic_selection.py",
    "backend/tests/test_provider_runtime_router.py",
    "backend/tests/test_provider_runtime_dispatcher_authority.py",
    "backend/tests/test_provider_runtime_bounded_audit_boundary.py",
    "backend/tests/test_webchat_polling_write_throttle.py",
    "backend/tests/test_background_job_transaction_boundary.py",
    "backend/tests/test_outbound_dispatch_transaction_boundary.py",
    "backend/tests/test_database_connection_budget.py",
    "backend/tests/test_database_pool_snapshot.py",
    "backend/tests/test_controlled_database_roles.py",
    "backend/tests/test_controlled_least_privilege.py",
    "backend/tests/test_supply_chain_qualification.py",
    "backend/tests/test_local_storage_backup_qualification.py",
    "backend/tests/test_queue_business_health.py",
    "backend/tests/test_release_readiness.py",
    "backend/tests/test_infrastructure_decision.py",
    "backend/tests/test_live_voice_credential_rotation_runbook.py",
)

MARKER_CONTRACTS = {
    "webapp/src/features/operator-workspace/OperatorWorkspaceActions.tsx": (
        "type CancelPreviewBinding",
        "cancelPreviewFingerprint(",
        "cancelPreview.fingerprint !== currentCancelFingerprint",
        "invalidateCancelPreview()",
    ),
    "backend/app/services/provider_runtime/traffic_selection.py": (
        "RUNTIME_ENABLED_ENV",
        '"control", "shadow", "canary", "full"',
        "stable_canary_bucket",
        "provider_runtime_disabled",
        "full_mode_configured",
    ),
    "backend/app/services/provider_runtime/router.py": (
        "from .traffic_selection import",
        "select_provider_traffic(",
        "ProviderTrafficPath.SHADOW_ONLY",
    ),
    "backend/app/db.py": (
        "DB_POOL_SIZE",
        "DB_MAX_OVERFLOW",
        "DB_POOL_TIMEOUT_SECONDS",
        "database_pool_configuration",
        "database_pool_snapshot",
        "pool_use_lifo",
    ),
    "backend/app/services/background_job_transaction_boundary.py": (
        "_claim_token",
        "_refresh_job_lease",
        "_owns_job_lease",
        "background_job_stale_completion_rejected",
    ),
    "backend/app/services/outbound_dispatch_transaction_boundary.py": (
        "_claim_token",
        "_refresh_message_lease",
        "_owns_message_lease",
        "reclaim_stale_processing_messages",
        "outbound_stale_completion_rejected",
    ),
    "backend/app/services/queue_health.py": (
        "nexus.queue-business-health.v1",
        "background_jobs_stale_processing",
        "outbound_stale_processing",
        "contains_payloads",
    ),
    "backend/app/services/release_readiness.py": (
        "nexus.release-readiness.v1",
        "production_authorized",
        "provider_enablement_authorized",
        "outbound_enablement_authorized",
    ),
    "scripts/qualification/database_capacity.py": (
        "nexus.database-capacity-snapshot.v1",
        "pg_stat_statements_available",
        "query_text_included",
        "within_budget",
    ),
    "scripts/qualification/supply_chain.py": (
        "nexus.supply-chain-qualification.v1",
        "EVIDENCE_DIR_ENV",
        "release_evidence_inside_candidate_tree",
        "candidate_tree_mutated",
        "--evidence-dir",
    ),
    "scripts/qualification/exact_head_acceptance.py": (
        "nexus.exact-head-acceptance-manifest.v1",
        "REQUIRED_ARTIFACTS",
        "assemble_acceptance_manifest",
        "qualify_acceptance_packet",
        "review_has_unresolved_findings",
        "repository_protection_missing",
    ),
    "scripts/qualification/postgres_acceptance.py": (
        "disposable_database_name_marker_required",
        "alembic_downgrade",
        "postgres_privacy_and_worker_tests",
        "database_url_included",
        "production_authorized",
    ),
    "deploy/docker-compose.controlled.yml": (
        "read_only: true",
        "no-new-privileges:true",
        "cap_drop:",
        "NEXUS_PROCESS_ROLE: web",
        "DB_POOL_SIZE_WEB",
        "DB_POOL_SIZE_OUTBOUND",
        "DB_POOL_SIZE_BACKGROUND",
        "DB_POOL_SIZE_WEBCHAT_AI",
        "DB_POOL_SIZE_HANDOFF",
    ),
}


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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repository_identity() -> dict[str, Any]:
    status = _git("status", "--porcelain")
    return {
        "schema": "nexus.candidate-identity.v1",
        "source_sha": _git("rev-parse", "HEAD"),
        "tree_sha": _git("rev-parse", "HEAD^{tree}"),
        "clean": not bool(status),
        "dirty_paths": status.splitlines()[:50],
        "file_sha256": {
            relative: _sha256(ROOT / relative)
            for relative in IDENTITY_FILES
            if (ROOT / relative).is_file()
        },
    }


def _load_json(relative: str, failures: list[str]) -> dict[str, Any] | None:
    try:
        payload = json.loads((ROOT / relative).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        failures.append(f"invalid JSON authority {relative}: {type(exc).__name__}")
        return None
    if not isinstance(payload, dict):
        failures.append(f"JSON authority must be an object: {relative}")
        return None
    return payload


def _qualification_failures(relative: str) -> list[str]:
    try:
        completed = subprocess.run(
            [sys.executable, relative],
            cwd=ROOT,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        return [f"qualification unavailable {relative}: {type(exc).__name__}"]
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
    failures: list[str] = []
    workflow_files = sorted(
        path.relative_to(ROOT).as_posix()
        for path in WORKFLOW_DIR.rglob("*")
        if path.is_file()
    ) if WORKFLOW_DIR.is_dir() else []
    if workflow_files != [CANONICAL_WORKFLOW]:
        failures.append(
            "exactly one canonical GitHub Actions workflow is required: "
            f"expected={[CANONICAL_WORKFLOW]} actual={workflow_files}"
        )
        return failures
    content = (ROOT / CANONICAL_WORKFLOW).read_text(encoding="utf-8")
    required = (
        "name: Canonical Acceptance",
        "pull_request:",
        "workflow_dispatch:",
        "permissions:",
        "contents: read",
        "required-gate:",
        "python scripts/verify_repository.py --static-only",
        "python scripts/qualification/postgres_acceptance.py",
        "npm run verify",
        "npm run e2e",
        "docker build",
    )
    for marker in required:
        if marker not in content:
            failures.append(f"canonical workflow marker missing: {marker}")
    for forbidden in ("pull_request_target:", "paths-ignore:", "secrets.", "continue-on-error: true"):
        if forbidden in content:
            failures.append(f"canonical workflow contains forbidden marker: {forbidden}")
    uses_lines = [line for line in content.splitlines() if re.match(r"^\s*-?\s*uses:", line)]
    for line in uses_lines:
        if not PINNED_ACTION.fullmatch(line):
            failures.append(f"workflow action is not pinned to a full SHA: {line.strip()}")
    job_count = len(re.findall(r"^  [a-zA-Z0-9_-]+:\s*$", content, re.MULTILINE))
    timeout_count = content.count("timeout-minutes:")
    if job_count == 0 or timeout_count < job_count:
        failures.append(
            f"every workflow job must set timeout-minutes: jobs={job_count} timeouts={timeout_count}"
        )
    return failures


def _compatibility_lifecycle_failures() -> list[str]:
    failures: list[str] = []
    payload = _load_json("config/architecture/compatibility-lifecycle.v1.json", failures)
    if payload is None:
        return failures
    if payload.get("schema") != "nexus.compatibility-lifecycle.v1":
        failures.append("compatibility lifecycle schema is invalid")
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
        if item.get("kind") in {"compose-alias", "environment-tombstone"}:
            if not item.get("replacement"):
                failures.append(f"compatibility replacement missing: {relative}")
            if not remove_after:
                failures.append(f"compatibility removal deadline missing: {relative}")
    return failures


def static_failures() -> list[str]:
    failures: list[str] = []
    failures.extend(_workflow_failures())
    for relative in RETIRED_PATHS:
        if (ROOT / relative).exists():
            failures.append(f"retired path exists: {relative}")
    for relative in ACTIONS_RESIDUE:
        path = ROOT / relative
        if path.is_dir() and any(path.iterdir()):
            failures.append(f"retired Actions authority directory exists: {relative}")
        elif path.is_file():
            failures.append(f"retired Actions authority residue exists: {relative}")
    for relative in REQUIRED_CANONICAL_PATHS:
        if not (ROOT / relative).is_file():
            failures.append(f"canonical authority missing: {relative}")

    failures.extend(_qualification_failures("scripts/qualification/service_authority.py"))
    failures.extend(_compatibility_lifecycle_failures())

    try:
        tracked_sql = [item for item in _git("ls-files", "*.sql").splitlines() if item.strip()]
    except (OSError, subprocess.CalledProcessError) as exc:
        failures.append(f"tracked SQL inventory unavailable: {type(exc).__name__}")
    else:
        if tracked_sql:
            failures.append(
                "Alembic is the only schema mutation authority; tracked raw SQL exists: "
                f"{tracked_sql}"
            )

    package_json = ROOT / "webapp/package.json"
    if package_json.is_file():
        try:
            scripts = json.loads(package_json.read_text(encoding="utf-8")).get("scripts", {})
        except json.JSONDecodeError:
            failures.append("webapp/package.json is invalid JSON")
        else:
            if "assert-http-transport-authority.mjs" not in str(scripts.get("architecture") or ""):
                failures.append("frontend architecture command omits transport authority gate")
            if "npm run architecture" not in str(scripts.get("verify") or ""):
                failures.append("frontend verify command bypasses architecture authority")

    for relative, canonical in PUBLIC_COMPATIBILITY.items():
        path = ROOT / relative
        if not path.is_file():
            failures.append(f"compatibility path missing: {relative}")
            continue
        content = path.read_text(encoding="utf-8")
        if canonical not in content:
            failures.append(f"compatibility path does not delegate to {canonical}: {relative}")
        if "UserRole" in content:
            failures.append(f"compatibility path owns role authorization: {relative}")
        functions = set(re.findall(r"^def\s+(\w+)", content, re.MULTILINE))
        if functions - {"__getattr__"}:
            failures.append(f"compatibility path owns business functions: {relative}")
        if len(content.splitlines()) > 24:
            failures.append(f"compatibility path grew into a second implementation: {relative}")

    for relative in ("deploy/docker-compose.server.yml", "deploy/docker-compose.candidate.yml"):
        path = ROOT / relative
        if path.is_file():
            content = path.read_text(encoding="utf-8")
            if "services:" in content or "include:" not in content:
                failures.append(f"compose compatibility alias owns topology: {relative}")

    workspace = ROOT / "webapp/src/features/operator-workspace/OperatorWorkspacePage.tsx"
    if workspace.is_file():
        content = workspace.read_text(encoding="utf-8")
        for marker in ("function AppNavigation", "operator-app-header", "/webchat?tab="):
            if marker in content:
                failures.append(f"workspace owns retired shell/navigation marker: {marker}")

    for relative, markers in MARKER_CONTRACTS.items():
        path = ROOT / relative
        if not path.is_file():
            failures.append(f"canonical authority missing: {relative}")
            continue
        content = path.read_text(encoding="utf-8")
        for marker in markers:
            if marker not in content:
                failures.append(f"canonical contract marker missing in {relative}: {marker}")

    supply_chain = ROOT / "scripts/qualification/supply_chain.py"
    if supply_chain.is_file() and 'ROOT / "artifacts" / "supply-chain"' in supply_chain.read_text(encoding="utf-8"):
        failures.append("release evidence path is inside the candidate repository")

    permissions = ROOT / "backend/app/services/permissions.py"
    if permissions.is_file():
        content = permissions.read_text(encoding="utf-8")
        if re.search(r"\bif\s+[^\n]*\.role\b", content):
            failures.append("runtime permission authority still branches on role names")
        if "ROLE_CAPABILITIES" not in content or "has_global_case_visibility" not in content:
            failures.append("central capability policy projection is incomplete")

    provider_router = ROOT / "backend/app/services/provider_runtime/router.py"
    if provider_router.is_file():
        content = provider_router.read_text(encoding="utf-8")
        if "def stable_canary_bucket" in content or "hashlib.sha256" in content:
            failures.append("Provider router owns a duplicate traffic selector")

    db_path = ROOT / "backend/app/db.py"
    if db_path.is_file():
        content = db_path.read_text(encoding="utf-8")
        if '"pool_size": 10' in content or '"max_overflow": 20' in content:
            failures.append("PostgreSQL pool budget is hard-coded to retired 10+20 values")

    dockerfile = ROOT / "Dockerfile"
    if dockerfile.is_file():
        effective = "\n".join(
            line for line in dockerfile.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )
        from_lines = [
            line.strip() for line in effective.splitlines()
            if line.strip().upper().startswith("FROM ")
        ]
        if any("@sha256:" not in line.split()[1] for line in from_lines):
            failures.append("Dockerfile contains an unpinned base image")
        if re.search(r"\bapk\s+upgrade\b", effective):
            failures.append("Dockerfile reintroduced mutable apk upgrade")

    requirements = ROOT / "backend/requirements.txt"
    if requirements.is_file():
        for number, line in enumerate(requirements.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("--"):
                continue
            if "==" not in stripped or any(marker in stripped for marker in (">=", "<=", "~=", "!=")):
                failures.append(f"Python requirement is not exact at line {number}")
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
    resolved.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _prepare_acceptance_directory(path: Path) -> Path:
    directory = path.expanduser().resolve()
    if _inside_repository(directory) or directory.is_symlink():
        raise ValueError("acceptance evidence directory must be outside candidate tree")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _argument_failures(args: argparse.Namespace) -> list[str]:
    failures: list[str] = []
    acceptance = args.acceptance_evidence_dir is not None
    if args.expected_sha and not SHA40.fullmatch(args.expected_sha):
        failures.append("expected SHA must be exactly 40 lowercase hex characters")
    if acceptance:
        if args.static_only:
            failures.append("final acceptance cannot use --static-only")
        if args.focused_backend:
            failures.append("final acceptance cannot use --focused-backend")
        if args.skip_browser:
            failures.append("final acceptance cannot use --skip-browser")
        if not args.expected_sha:
            failures.append("final acceptance requires --expected-sha")
        if args.release_evidence_dir is None:
            failures.append("final acceptance requires --release-evidence-dir")
        if not args.acceptance_database_url:
            failures.append("final acceptance requires --acceptance-database-url or DATABASE_URL")
        if args.acceptance_upload_source is None or args.acceptance_upload_backup is None:
            failures.append("final acceptance requires upload source and backup paths")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the single canonical Nexus implementation. Repository scripts own "
            "verification rules; one canonical GitHub Actions workflow owns remote execution."
        )
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
    args = parser.parse_args()

    started_at = datetime.now(timezone.utc).isoformat()
    argument_failures = _argument_failures(args)
    if argument_failures:
        payload = {
            "schema": "nexus.canonical-verification.v1",
            "status": "fail",
            "stage": "arguments",
            "started_at": started_at,
            "failures": argument_failures,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        _write_evidence(args.evidence_out, payload)
        return 1

    acceptance_dir: Path | None = None
    try:
        if args.acceptance_evidence_dir is not None:
            acceptance_dir = _prepare_acceptance_directory(args.acceptance_evidence_dir)
        start_identity = repository_identity()
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        payload = {
            "schema": "nexus.canonical-verification.v1",
            "status": "fail",
            "stage": "candidate_identity",
            "started_at": started_at,
            "error_type": type(exc).__name__,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        _write_evidence(args.evidence_out, payload)
        return 1

    failures = static_failures()
    if not start_identity["clean"]:
        failures.append("candidate working tree is not clean before verification")
    if args.expected_sha and start_identity["source_sha"] != args.expected_sha:
        failures.append(
            f"candidate source SHA mismatch: expected={args.expected_sha} "
            f"actual={start_identity['source_sha']}"
        )
    static_payload = {"static_ok": not failures, "failures": failures, "candidate": start_identity}
    print(json.dumps(static_payload, ensure_ascii=False, indent=2))
    if failures:
        payload = {
            "schema": "nexus.canonical-verification.v1",
            "status": "fail",
            "stage": "static",
            "started_at": started_at,
            **static_payload,
        }
        _write_evidence(args.evidence_out, payload)
        return 1

    acceptance_qualification_path: str | None = None
    try:
        supply_chain_command = [sys.executable, "scripts/qualification/supply_chain.py"]
        if args.release_evidence_dir:
            supply_chain_command.extend(["--release", "--evidence-dir", str(args.release_evidence_dir.resolve())])
        if acceptance_dir is not None:
            supply_chain_command.extend(["--output", str(acceptance_dir / "supply-chain.json")])
        run("supply_chain", supply_chain_command)

        if not args.static_only:
            run("service_authority", [sys.executable, "scripts/qualification/service_authority.py"])
            run("route_authority", [sys.executable, "scripts/qualification/route_authority.py"])
            run("alembic_heads", [sys.executable, "-m", "alembic", "heads"], cwd=ROOT / "backend")
            run("frontend_install", ["npm", "ci", "--ignore-scripts"], cwd=ROOT / "webapp")
            run("frontend_verify", ["npm", "run", "verify"], cwd=ROOT / "webapp")
            run(
                "python_compile",
                [sys.executable, "-m", "compileall", "backend/app", "backend/scripts", "scripts/qualification", "scripts/release"],
            )
            backend_tests = list(FOCUSED_BACKEND_TESTS) if args.focused_backend else ["backend/tests"]
            run("backend_tests", [sys.executable, "-m", "pytest", "-q", *backend_tests])
            if not args.skip_browser:
                run("browser_tests", ["npm", "run", "e2e"], cwd=ROOT / "webapp")

        if acceptance_dir is not None:
            postgres_env = os.environ.copy()
            postgres_env["DATABASE_URL"] = args.acceptance_database_url
            postgres_command = [
                sys.executable,
                "scripts/qualification/postgres_acceptance.py",
                "--evidence-dir",
                str(acceptance_dir),
                "--source-sha",
                start_identity["source_sha"],
                "--tree-sha",
                start_identity["tree_sha"],
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
                "upload_backup",
                [
                    sys.executable,
                    "scripts/qualification/local_storage_backup.py",
                    "--source",
                    str(args.acceptance_upload_source.resolve()),
                    "--backup",
                    str(args.acceptance_upload_backup.resolve()),
                    "--output",
                    str(acceptance_dir / "upload-backup.json"),
                ],
            )
            run(
                "infrastructure_decisions",
                [
                    sys.executable,
                    "scripts/qualification/infrastructure_decision.py",
                    "--database",
                    str(acceptance_dir / "database-capacity.json"),
                    "--queue",
                    str(acceptance_dir / "queue-baseline.json"),
                    "--realtime",
                    str(acceptance_dir / "realtime-baseline.json"),
                    "--storage",
                    str(acceptance_dir / "storage-baseline.json"),
                    "--output",
                    str(acceptance_dir / "infrastructure-decisions.json"),
                ],
            )
            acceptance_output = acceptance_dir / "acceptance-qualification.json"
            run(
                "exact_head_acceptance",
                [
                    sys.executable,
                    "scripts/qualification/exact_head_acceptance.py",
                    "--evidence-dir",
                    str(acceptance_dir),
                    "--source-sha",
                    start_identity["source_sha"],
                    "--tree-sha",
                    start_identity["tree_sha"],
                    "--manifest-name",
                    args.acceptance_manifest_name,
                    "--assemble-manifest",
                    "--output",
                    str(acceptance_output),
                ],
            )
            acceptance_qualification_path = str(acceptance_output)
    except VerificationCommandError as exc:
        payload = {
            "schema": "nexus.canonical-verification.v1",
            "status": "fail",
            "stage": exc.stage,
            "return_code": exc.return_code,
            "started_at": started_at,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "candidate_start": start_identity,
            "acceptance_mode": acceptance_dir is not None,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        _write_evidence(args.evidence_out, payload)
        return 1

    try:
        end_identity = repository_identity()
    except (OSError, subprocess.CalledProcessError) as exc:
        payload = {
            "schema": "nexus.canonical-verification.v1",
            "status": "fail",
            "stage": "final_identity",
            "error_type": type(exc).__name__,
            "started_at": started_at,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "candidate_start": start_identity,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        _write_evidence(args.evidence_out, payload)
        return 1

    identity_equal = (
        start_identity["source_sha"] == end_identity["source_sha"]
        and start_identity["tree_sha"] == end_identity["tree_sha"]
        and start_identity["file_sha256"] == end_identity["file_sha256"]
        and end_identity["clean"]
    )
    payload = {
        "schema": "nexus.canonical-verification.v1",
        "status": "pass" if identity_equal else "fail",
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "static_only": args.static_only,
        "focused_backend": args.focused_backend,
        "browser_executed": not args.static_only and not args.skip_browser,
        "release_evidence_checked": bool(args.release_evidence_dir),
        "acceptance_mode": acceptance_dir is not None,
        "acceptance_evidence_checked": acceptance_dir is not None,
        "acceptance_qualification_path": acceptance_qualification_path,
        "same_identity": identity_equal,
        "candidate_start": start_identity,
        "candidate_end": end_identity,
        "remote_execution_authority": CANONICAL_WORKFLOW,
        "production_authorized": False,
        "provider_enablement_authorized": False,
        "outbound_enablement_authorized": False,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    _write_evidence(args.evidence_out, payload)
    return 0 if identity_equal else 1


if __name__ == "__main__":
    raise SystemExit(main())
