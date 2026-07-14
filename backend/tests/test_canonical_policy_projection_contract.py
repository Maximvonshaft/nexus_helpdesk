from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCOPE_SOURCE = ROOT / "backend/app/services/operator_queue_scope.py"
PERMISSIONS_SOURCE = ROOT / "backend/app/services/permissions.py"
WORKSPACE_API_SOURCE = ROOT / "webapp/src/lib/operatorWorkspaceApi.ts"


def _read(path: Path) -> str:
    assert path.is_file(), f"required authority path is missing: {path}"
    return path.read_text(encoding="utf-8")


def test_operator_scope_authority_is_capability_and_grant_driven_not_role_driven() -> None:
    source = _read(SCOPE_SOURCE)

    assert "UserRole" not in source
    assert "current_user.role" not in source
    assert "getattr(current_user, 'role'" not in source
    assert "_team_country" not in source
    assert "requires_explicit_admin_scope" not in source

    assert "ensure_capability(" in source
    assert "CAP_OPERATOR_QUEUE_READ" in source
    assert "active_scope_grant(" in source
    assert "operator_queue_scope_not_granted" in source


def test_scope_version_uses_server_policy_fingerprint_not_role_strings() -> None:
    source = _read(SCOPE_SOURCE)
    permissions = _read(PERMISSIONS_SOURCE)

    assert "role:" not in source
    assert "capability_fingerprint" in source
    assert "def capability_fingerprint" in permissions
    assert "sorted(resolve_capabilities(user, db))" in permissions


def test_normal_workspace_scope_has_no_environment_or_manual_authority() -> None:
    source = _read(WORKSPACE_API_SOURCE)

    for forbidden in (
        "VITE_NEXUS_TENANT_KEY",
        "VITE_NEXUS_COUNTRY_CODE",
        "VITE_NEXUS_CHANNEL_KEY",
        "saveWorkspaceScope",
        "WORKSPACE_SCOPE_STORAGE_KEY",
    ):
        assert forbidden not in source

    assert "currentScopes" in source
    assert "AuthorizedWorkspaceScopesResponse" in source


def test_server_derived_scope_projection_remains_fail_closed() -> None:
    source = _read(WORKSPACE_API_SOURCE)

    assert "authorized" in source.lower()
    assert "no authorized" in source.lower() or "未授权" in source or "无可用" in source
