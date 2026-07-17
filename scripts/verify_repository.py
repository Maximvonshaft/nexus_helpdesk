#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github/workflows"

RETIRED_PATHS = (
    "frontend",
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
    "webapp/package-lock.json",
    "webapp/src/app/AppShell.tsx",
    "webapp/src/app/navigation.ts",
    "webapp/src/app/OperatorPresentation.tsx",
    "webapp/src/theme/nexusTheme.ts",
    "webapp/src/theme/NexusThemeProvider.tsx",
    "webapp/src/features/knowledge/KnowledgePage.tsx",
    "webapp/src/features/operator-workspace/OperatorWorkspacePage.tsx",
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
    "backend/app/services/canonical_route_projection.py",
    "backend/app/services/canonical_ticket_service.py",
    "backend/app/services/canonical_control_tower_service.py",
    "backend/app/services/canonical_qa_training_service.py",
    "backend/app/services/canonical_operator_work_queue.py",
    "backend/app/services/canonical_webchat_handoff_service.py",
    "backend/app/api/canonical_osr_admin.py",
    "backend/app/api/canonical_integration.py",
    "deploy/docker-compose.controlled.yml",
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

FORBIDDEN_WORKSPACE_MARKERS = (
    "function AppNavigation",
    "operator-app-header",
    "/webchat?tab=",
)

IDENTITY_FILES = (
    "backend/requirements.txt",
    "webapp/package.json",
    "webapp/package-lock.json",
    "Dockerfile",
    "deploy/docker-compose.controlled.yml",
)


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repository_identity() -> dict[str, Any]:
    head = _git("rev-parse", "HEAD")
    tree = _git("rev-parse", "HEAD^{tree}")
    status = _git("status", "--porcelain")
    hashes = {
        relative: _sha256(ROOT / relative)
        for relative in IDENTITY_FILES
        if (ROOT / relative).is_file()
    }
    return {
        "schema": "nexus.candidate-identity.v1",
        "source_sha": head,
        "tree_sha": tree,
        "clean": not bool(status),
        "dirty_paths": status.splitlines()[:50],
        "file_sha256": hashes,
    }


def _require_markers(
    failures: list[str],
    relative: str,
    markers: tuple[str, ...],
) -> None:
    path = ROOT / relative
    if not path.is_file():
        failures.append(f"canonical authority missing: {relative}")
        return
    content = path.read_text(encoding="utf-8")
    for marker in markers:
        if marker not in content:
            failures.append(f"canonical contract marker missing in {relative}: {marker}")


def static_failures() -> list[str]:
    failures: list[str] = []

    workflow_files = {
        path.relative_to(ROOT).as_posix()
        for path in WORKFLOW_DIR.rglob("*")
        if path.is_file()
    } if WORKFLOW_DIR.is_dir() else set()
    if workflow_files:
        failures.append(
            "GitHub Actions are retired and the workflows directory must contain no files: "
            f"actual={sorted(workflow_files)}"
        )

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
        if len(content.splitlines()) > 20:
            failures.append(f"compatibility path grew into a second implementation: {relative}")

    workspace = ROOT / "webapp/src/features/operator-workspace/OperatorWorkspacePage.tsx"
    if workspace.is_file():
        content = workspace.read_text(encoding="utf-8")
        for marker in FORBIDDEN_WORKSPACE_MARKERS:
            if marker in content:
                failures.append(f"workspace owns retired shell/navigation marker: {marker}")
        required_cancel_markers = (
            "type CancelPreviewBinding",
            "cancelPreviewFingerprint(",
            "cancelPreview.fingerprint !== currentCancelFingerprint",
            "invalidateCancelPreview()",
        )
        for marker in required_cancel_markers:
            if marker not in content:
                failures.append(f"cancel preview binding contract missing: {marker}")

    permissions = ROOT / "backend/app/services/permissions.py"
    if permissions.is_file():
        content = permissions.read_text(encoding="utf-8")
        if re.search(r"\bif\s+[^\n]*\.role\b", content):
            failures.append("runtime permission authority still branches on role names")
        if "ROLE_CAPABILITIES" not in content or "has_global_case_visibility" not in content:
            failures.append("central capability policy projection is incomplete")

    runtime_api = ROOT / "backend/app/api/admin_provider_runtime.py"
    if runtime_api.is_file():
        content = runtime_api.read_text(encoding="utf-8")
        if content.count("ensure_can_read_runtime(current_user, db)") < 2:
            failures.append("runtime read endpoints do not use the read-only capability authority")
        if "ensure_can_manage_runtime(current_user, db)" not in content:
            failures.append("runtime mutation endpoint lost manage authority")

    control_tower = ROOT / "webapp/src/features/control-tower/ControlTowerPage.tsx"
    if control_tower.is_file():
        content = control_tower.read_text(encoding="utf-8")
        for legacy in ("/accounts", "/outbound-email", "/ai-control"):
            if legacy in content:
                failures.append(f"frontend still guesses legacy control-tower href: {legacy}")

    _require_markers(
        failures,
        "backend/app/services/provider_runtime/traffic_selection.py",
        (
            "RUNTIME_ENABLED_ENV",
            '"control", "shadow", "canary", "full"',
            "stable_canary_bucket",
            "provider_runtime_disabled",
            "full_mode_configured",
        ),
    )
    _require_markers(
        failures,
        "backend/app/services/provider_runtime/router.py",
        (
            "from .traffic_selection import",
            "select_provider_traffic(",
            "ProviderTrafficPath.SHADOW_ONLY",
        ),
    )
    router_path = ROOT / "backend/app/services/provider_runtime/router.py"
    if router_path.is_file():
        router_content = router_path.read_text(encoding="utf-8")
        if "def stable_canary_bucket" in router_content or "hashlib.sha256" in router_content:
            failures.append("Provider router owns a duplicate traffic selector")

    _require_markers(
        failures,
        "backend/app/db.py",
        (
            "DB_POOL_SIZE",
            "DB_MAX_OVERFLOW",
            "DB_POOL_TIMEOUT_SECONDS",
            "database_pool_configuration",
            "pool_use_lifo",
        ),
    )
    db_path = ROOT / "backend/app/db.py"
    if db_path.is_file():
        db_content = db_path.read_text(encoding="utf-8")
        if '"pool_size": 10' in db_content or '"max_overflow": 20' in db_content:
            failures.append("PostgreSQL pool budget is hard-coded to the retired 10+20 values")

    _require_markers(
        failures,
        "deploy/docker-compose.controlled.yml",
        (
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
    )

    return failures


def run(command: list[str], *, cwd: Path | None = None) -> None:
    print(f"+ {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=cwd or ROOT, check=True)


def _write_evidence(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the single canonical Nexus implementation with GitHub Actions retired."
    )
    parser.add_argument("--static-only", action="store_true", help="Run repository structure checks only.")
    parser.add_argument("--skip-browser", action="store_true", help="Skip Playwright browser journeys.")
    parser.add_argument("--focused-backend", action="store_true", help="Run the focused backend acceptance suite instead of every backend test.")
    parser.add_argument("--evidence-out", type=Path, help="Write the same-identity verification result as JSON.")
    args = parser.parse_args()

    started_at = datetime.now(timezone.utc).isoformat()
    try:
        start_identity = repository_identity()
    except (OSError, subprocess.CalledProcessError) as exc:
        payload = {
            "schema": "nexus.canonical-verification.v1",
            "status": "fail",
            "reason": "candidate_identity_unavailable",
            "error_type": type(exc).__name__,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        _write_evidence(args.evidence_out, payload)
        return 1

    failures = static_failures()
    if not start_identity["clean"]:
        failures.append("candidate working tree is not clean before verification")

    static_payload = {
        "static_ok": not failures,
        "failures": failures,
        "candidate": start_identity,
    }
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

    if not args.static_only:
        run(["npm", "ci", "--ignore-scripts"], cwd=ROOT / "webapp")
        run(["npm", "run", "verify"], cwd=ROOT / "webapp")
        run([sys.executable, "-m", "compileall", "backend/app", "backend/scripts"])

        if args.focused_backend:
            backend_tests = [
                "backend/tests/test_canonical_service_authorities.py",
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
            ]
        else:
            backend_tests = ["backend/tests"]
        run([sys.executable, "-m", "pytest", "-q", *backend_tests])

        if not args.skip_browser:
            run(["npm", "run", "e2e"], cwd=ROOT / "webapp")

    end_identity = repository_identity()
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
        "same_identity": identity_equal,
        "candidate_start": start_identity,
        "candidate_end": end_identity,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    _write_evidence(args.evidence_out, payload)
    return 0 if identity_equal else 1


if __name__ == "__main__":
    raise SystemExit(main())
