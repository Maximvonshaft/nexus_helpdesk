from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    (ROOT / path).write_text(content, encoding="utf-8")


def replace_once(content: str, old: str, new: str, *, label: str) -> str:
    count = content.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return content.replace(old, new, 1)


def pre() -> None:
    path = "webapp/src/features/operator-workspace/OperatorWorkspacePage.tsx"
    source = read(path)
    source = replace_once(
        source,
        "import { operatorWorkspaceApi, loadWorkspaceScope, saveWorkspaceScope } from '@/lib/operatorWorkspaceApi'",
        "import { loadWorkspaceScope, operatorWorkspaceApi, saveWorkspaceScope } from '@/lib/operatorWorkspaceApi'",
        label="normalize workspace import",
    )
    write(path, source)


def post() -> None:
    settings_path = "backend/app/settings.py"
    settings = read(settings_path)
    settings = replace_once(
        settings,
        """        self.legacy_frontend_root = self.project_root / \"frontend\"\n        self.frontend_dist_root = self.project_root / \"frontend_dist\"\n        self.frontend_dist_index = self.frontend_dist_root / \"index.html\"\n        self.frontend_dist_available = self.frontend_dist_index.exists()\n        self.frontend_root = self.frontend_dist_root if self.frontend_dist_available else self.legacy_frontend_root\n        self.frontend_uses_legacy_fallback = not self.frontend_dist_available\n""",
        """        self.frontend_dist_root = self.project_root / \"frontend_dist\"\n        self.frontend_dist_index = self.frontend_dist_root / \"index.html\"\n        self.frontend_dist_available = self.frontend_dist_index.exists()\n        self.frontend_root = self.frontend_dist_root\n""",
        label="remove legacy frontend selection",
    )
    settings = settings.replace(
        "frontend_dist/index.html must exist in production; refusing legacy frontend fallback",
        "frontend_dist/index.html must exist in production",
    )
    if "legacy_frontend_root" in settings or "frontend_uses_legacy_fallback" in settings:
        raise SystemExit("legacy frontend fallback remains in settings")
    write(settings_path, settings)

    main_path = "backend/app/main.py"
    main = read(main_path)
    main = replace_once(
        main,
        """def _frontend_readiness() -> dict[str, object]:\n    active_index = settings.frontend_root / 'index.html'\n    dist_index_exists = settings.frontend_dist_index.exists()\n    active_index_exists = active_index.exists()\n    return {\n        'ok': active_index_exists and (settings.app_env != 'production' or dist_index_exists),\n        'active_root': 'legacy' if settings.frontend_uses_legacy_fallback else 'frontend_dist',\n        'frontend_dist_index': 'present' if dist_index_exists else 'missing',\n    }\n""",
        """def _frontend_readiness() -> dict[str, object]:\n    dist_index_exists = settings.frontend_dist_index.exists()\n    return {\n        'ok': dist_index_exists,\n        'active_root': 'frontend_dist',\n        'frontend_dist_index': 'present' if dist_index_exists else 'missing',\n    }\n""",
        label="canonical frontend readiness",
    )
    if "frontend_uses_legacy_fallback" in main or "'active_root': 'legacy'" in main:
        raise SystemExit("legacy frontend fallback remains in main")
    write(main_path, main)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "pre":
        pre()
    elif mode == "post":
        post()
    else:
        raise SystemExit("expected pre or post")
