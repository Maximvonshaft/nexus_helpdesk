from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
_SCANNED_ROOTS = (
    ROOT / ".github" / "workflows",
    ROOT / "backend" / "app",
    ROOT / "backend" / "scripts",
    ROOT / "backend" / ".env.example",
    ROOT / "connectors" / "whatsapp-sidecar" / "src",
    ROOT / "deploy",
    ROOT / "scripts" / "deploy",
    ROOT / "scripts" / "qualification",
    ROOT / "scripts" / "release",
    ROOT / "webapp" / "src",
)
_TEXT_SUFFIXES = {
    ".css",
    ".example",
    ".html",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".py",
    ".sh",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
_SELF = Path(__file__).resolve()

# Match executable configuration/import/route surfaces only. Security preflight,
# rollback and deployment-authority code must remain free to name retired values
# in explicit rejection and cleanup lists.
_ACTIVE_RESIDUE_PATTERNS = {
    "legacy_env_assignment": re.compile(
        r"(?m)^\s*(?:-\s*e\s+)?(?:WHATSAPP_NATIVE_ENABLED|WHATSAPP_DISPATCH_MODE|WHATSAPP_SIDECAR_URL|WHATSAPP_SIDECAR_TOKEN)\s*[:=]"
    ),
    "legacy_python_import": re.compile(
        r"(?m)^\s*(?:from|import)\s+[^\n]*(?:whatsapp_native|outbound_adapters\.whatsapp_native)"
    ),
    "legacy_runtime_symbol": re.compile(
        r"\b(?:ingest_whatsapp_native_inbound|dispatch_whatsapp_native_outbound|whatsappNativeStatus)\b"
    ),
    "legacy_admin_route": re.compile(
        r"/api/admin/whatsapp/accounts(?:/|['\"`])"
    ),
    "legacy_integration_route": re.compile(
        r"/api/integrations/whatsapp/native(?:/|['\"`])"
    ),
    "legacy_worker_service": re.compile(
        r"(?m)^\s*worker-handoff-snapshot(?:-controlled|-rc)?:\s*$"
    ),
    "legacy_worker_command": re.compile(
        r"--queue(?:=|\s+)handoff-snapshot\b"
    ),
}


def test_retired_whatsapp_and_handoff_runtime_residues_are_absent() -> None:
    findings: list[str] = []
    for root in _SCANNED_ROOTS:
        candidates = (root,) if root.is_file() else root.rglob("*") if root.exists() else ()
        for path in candidates:
            if (
                not path.is_file()
                or path.is_symlink()
                or path.resolve() == _SELF
                or path.suffix.lower() not in _TEXT_SUFFIXES
                or "node_modules" in path.parts
                or "dist" in path.parts
                or "tests" in path.parts
            ):
                continue
            text = path.read_text(encoding="utf-8", errors="strict")
            for label, pattern in _ACTIVE_RESIDUE_PATTERNS.items():
                if pattern.search(text):
                    findings.append(f"{path.relative_to(ROOT)}:{label}")
    assert findings == [], "retired runtime residue:\n" + "\n".join(findings)


def test_only_canonical_whatsapp_frontend_api_surface_exists() -> None:
    legacy_api = ROOT / "webapp" / "src" / "lib" / "supportApi.ts"
    canonical_api = ROOT / "webapp" / "src" / "lib" / "whatsappApi.ts"
    assert canonical_api.is_file()
    legacy_source = legacy_api.read_text(encoding="utf-8")
    canonical_source = canonical_api.read_text(encoding="utf-8")
    assert "WhatsAppNativeAccountStatus" not in legacy_source
    assert "whatsappNativeStatus" not in legacy_source
    assert "/api/admin/whatsapp/accounts" not in legacy_source
    assert "/api/admin/whatsapp/connections" in canonical_source
    assert "/api/admin/whatsapp/embedded-signup" in canonical_source
    assert "/native" not in canonical_source


def test_retired_files_are_physically_absent() -> None:
    retired = (
        "backend/app/api/admin_whatsapp_native.py",
        "backend/app/api/whatsapp_native_integration.py",
        "backend/app/services/whatsapp_native_admin.py",
        "backend/app/services/whatsapp_native_inbound.py",
        "backend/app/services/outbound_adapters/whatsapp_native.py",
        "backend/tests/test_admin_whatsapp_native_api.py",
        "backend/tests/test_whatsapp_native_inbound_integration.py",
        "backend/tests/test_whatsapp_native_outbound_adapter.py",
        "deploy/docker-compose.whatsapp-sidecar.example.yml",
    )
    for relative in retired:
        assert not (ROOT / relative).exists(), relative
