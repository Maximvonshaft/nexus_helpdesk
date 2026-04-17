from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.settings import get_settings  # noqa: E402


def main() -> int:
    settings = get_settings()
    warnings: list[str] = []
    if not settings.is_postgres:
        warnings.append("DATABASE_URL is not PostgreSQL")
    if settings.storage_backend == "local":
        warnings.append("STORAGE_BACKEND is local")
    if settings.openclaw_transport != "mcp":
        warnings.append("OPENCLAW_TRANSPORT is not mcp")
    if settings.metrics_enabled and not settings.metrics_token:
        warnings.append("METRICS_ENABLED=true but METRICS_TOKEN is missing")
    if settings.openclaw_attachment_url_fetch_enabled and not settings.openclaw_attachment_allowed_hosts:
        warnings.append("OPENCLAW_ATTACHMENT_URL_FETCH_ENABLED=true but OPENCLAW_ATTACHMENT_ALLOWED_HOSTS is empty")
    payload = {
        "app_env": settings.app_env,
        "database_url_scheme": settings.database_url.split(":", 1)[0],
        "is_postgres": settings.is_postgres,
        "storage_backend": settings.storage_backend,
        "openclaw_transport": settings.openclaw_transport,
        "metrics_enabled": settings.metrics_enabled,
        "metrics_token_configured": bool(settings.metrics_token),
        "openclaw_sync_enabled": settings.openclaw_sync_enabled,
        "openclaw_attachment_url_fetch_enabled": settings.openclaw_attachment_url_fetch_enabled,
        "openclaw_attachment_allowed_hosts": settings.openclaw_attachment_allowed_hosts,
        "warnings": warnings,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not warnings else 2


if __name__ == "__main__":
    raise SystemExit(main())
