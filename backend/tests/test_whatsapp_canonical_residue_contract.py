from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
_SCANNED_ROOTS = (
    ROOT / ".github" / "workflows",
    ROOT / "backend" / "app",
    ROOT / "backend" / "scripts",
    ROOT / "connectors" / "whatsapp-sidecar" / "src",
    ROOT / "deploy",
    ROOT / "scripts" / "deploy",
    ROOT / "scripts" / "qualification",
    ROOT / "scripts" / "release",
    ROOT / "webapp" / "src",
)
_FORBIDDEN = (
    "WHATSAPP_NATIVE_ENABLED",
    "WHATSAPP_DISPATCH_MODE",
    "WHATSAPP_SIDECAR_URL",
    "WHATSAPP_SIDECAR_TOKEN",
    "whatsapp_native_",
    "/api/admin/whatsapp/accounts",
    "/api/integrations/whatsapp/native",
    "docker-compose.whatsapp-sidecar.example.yml",
    "worker-handoff-snapshot",
    "NEXUS_WORKER_QUEUE: handoff-snapshot",
)
_TEXT_SUFFIXES = {
    ".css",
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


def test_retired_whatsapp_and_handoff_runtime_residues_are_absent() -> None:
    findings: list[str] = []
    for root in _SCANNED_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
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
            for token in _FORBIDDEN:
                if token in text:
                    findings.append(f"{path.relative_to(ROOT)}:{token}")
    assert findings == [], "retired runtime residue:\n" + "\n".join(findings)


def test_only_canonical_whatsapp_frontend_api_surface_exists() -> None:
    api = ROOT / "webapp" / "src" / "lib" / "whatsappApi.ts"
    assert api.is_file()
    source = api.read_text(encoding="utf-8")
    assert "/api/admin/whatsapp/connections" in source
    assert "/api/admin/whatsapp/embedded-signup" in source
    assert "/native" not in source


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
