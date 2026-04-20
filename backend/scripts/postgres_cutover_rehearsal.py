from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, text  # noqa: E402

from app.db import engine  # noqa: E402
from app.settings import get_settings  # noqa: E402


REQUIRED_TABLES = [
    "tickets",
    "ticket_outbound_messages",
    "background_jobs",
    "openclaw_conversation_links",
    "openclaw_transcript_messages",
    "integration_clients",
]


def main() -> int:
    settings = get_settings()
    inspector = inspect(engine)
    payload = {
        "app_env": settings.app_env,
        "database_url_scheme": settings.database_url.split(":", 1)[0],
        "is_postgres": settings.is_postgres,
        "storage_backend": settings.storage_backend,
        "openclaw_transport": settings.openclaw_transport,
        "checks": {},
        "warnings": [],
    }
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        payload["checks"]["database_connection"] = True
    except Exception as exc:
        payload["checks"]["database_connection"] = False
        payload["warnings"].append(f"database_connection_failed:{exc}")

    try:
        tables = set(inspector.get_table_names())
    except Exception as exc:
        tables = set()
        payload["warnings"].append(f"inspect_failed:{exc}")

    missing = [name for name in REQUIRED_TABLES if name not in tables]
    payload["checks"]["required_tables_present"] = not missing
    if missing:
        payload["warnings"].append("missing_tables:" + ",".join(missing))

    if "alembic_version" in tables:
        payload["checks"]["alembic_version_present"] = True
        try:
            with engine.connect() as conn:
                versions = [row[0] for row in conn.execute(text("SELECT version_num FROM alembic_version")).all()]
            payload["alembic_versions"] = versions
        except Exception as exc:
            payload["warnings"].append(f"alembic_version_read_failed:{exc}")
    else:
        payload["checks"]["alembic_version_present"] = False
        payload["warnings"].append("alembic_version_table_missing")

    payload["checks"]["postgres_required"] = settings.is_postgres
    if not settings.is_postgres:
        payload["warnings"].append("database_not_postgres")

    payload["checks"]["mcp_preferred"] = settings.openclaw_transport == "mcp"
    if settings.openclaw_transport != "mcp":
        payload["warnings"].append("openclaw_transport_not_mcp")

    payload["checks"]["object_storage_preferred"] = settings.storage_backend != "local"
    if settings.storage_backend == "local":
        payload["warnings"].append("storage_backend_local")

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not payload["warnings"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
