#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RETIRED_PATHS = (
    "frontend",
    "webapp/src/features/support-console",
    "webapp/src/shared/ui",
    "webapp/src/shared/api",
    "webapp/src/lib/api.ts",
    "webapp/src/lib/webchatRealtime.ts",
)

ACTIONS_RESIDUE = (
    ".github/workflows",
    "config/governance/actions-authority.v1.json",
    "config/governance/release-candidate-preconditions.v1.json",
    "scripts/ci/actions_authority_inventory.py",
    "scripts/release/exact_main_candidate_preconditions.py",
    "docs/superpowers/plans/2026-07-14-actions-authority-convergence.md",
)

REQUIRED_CANONICAL_PATHS = (
    "webapp/src/app/AppShell.tsx",
    "webapp/src/app/navigation.ts",
    "webapp/src/lib/apiClient.ts",
    "webapp/src/styles/tokens.css",
    "webapp/src/components/ui/Button.tsx",
    "webapp/src/domain/operationalPresentation.ts",
    "backend/app/services/permissions.py",
    "backend/app/services/canonical_route_projection.py",
    "backend/app/services/canonical_ticket_service.py",
    "backend/app/services/canonical_control_tower_service.py",
    "backend/app/services/canonical_qa_training_service.py",
    "backend/app/services/canonical_operator_work_queue.py",
    "backend/app/services/canonical_webchat_handoff_service.py",
    "backend/app/api/canonical_osr_admin.py",
    "backend/app/api/canonical_integration.py",
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


def static_failures() -> list[str]:
    failures: list[str] = []
    for relative in RETIRED_PATHS:
        if (ROOT / relative).exists():
            failures.append(f"retired path exists: {relative}")
    for relative in ACTIONS_RESIDUE:
        path = ROOT / relative
        if path.is_dir() and any(path.iterdir()):
            failures.append(f"GitHub Actions workflow residue exists: {relative}")
        elif path.is_file():
            failures.append(f"GitHub Actions authority residue exists: {relative}")
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
            "cancelFingerprint(",
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

    return failures


def run(command: list[str], *, cwd: Path | None = None) -> None:
    print(f"+ {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=cwd or ROOT, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the single canonical Nexus implementation without GitHub Actions.")
    parser.add_argument("--static-only", action="store_true", help="Run repository structure checks only.")
    parser.add_argument("--skip-browser", action="store_true", help="Skip Playwright browser journeys.")
    parser.add_argument("--focused-backend", action="store_true", help="Run the focused backend acceptance suite instead of every backend test.")
    args = parser.parse_args()

    failures = static_failures()
    print(json.dumps({"static_ok": not failures, "failures": failures}, ensure_ascii=False, indent=2))
    if failures:
        return 1
    if args.static_only:
        return 0

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
        ]
    else:
        backend_tests = ["backend/tests"]
    run([sys.executable, "-m", "pytest", "-q", *backend_tests])

    if not args.skip_browser:
        run(["npm", "run", "e2e"], cwd=ROOT / "webapp")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
