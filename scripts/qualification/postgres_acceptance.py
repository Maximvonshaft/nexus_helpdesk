#!/usr/bin/env python3
"""Run bounded PostgreSQL acceptance against an explicitly disposable database."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.qualification.database_capacity import (  # noqa: E402
    ProcessPool,
    calculate_connection_budget,
    collect_postgresql_snapshot,
)

DISPOSABLE_MARKERS = ("test", "acceptance", "scratch", "ci", "tmp")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "postgres", "postgres-controlled", "db"}
POSTGRES_TESTS = (
    "backend/tests/test_support_conversations_postgres.py",
    "backend/tests/test_support_conversation_privacy.py",
    "backend/tests/test_support_sensitive_access.py",
    "backend/tests/resilience/test_postgres_worker_recovery.py",
)
FAIL_CLOSED_ENV = {
    "APP_ENV": "test",
    "AUTO_INIT_DB": "false",
    "SEED_DEMO_DATA": "false",
    "ALLOW_DEV_AUTH": "false",
    "PROVIDER_RUNTIME_ENABLED": "false",
    "PROVIDER_RUNTIME_TRAFFIC_MODE": "control",
    "PROVIDER_RUNTIME_KILL_SWITCH": "true",
    "PROVIDER_RUNTIME_CANARY_PERCENT": "0",
    "PRIVATE_AI_RUNTIME_ENABLED": "false",
    "WEBCHAT_AI_ENABLED": "false",
    "WEBCHAT_AI_AUTO_REPLY_MODE": "off",
    "WEBCHAT_VOICE_ENABLED": "false",
    "ENABLE_OUTBOUND_DISPATCH": "false",
    "OUTBOUND_PROVIDER": "disabled",
    "WHATSAPP_NATIVE_ENABLED": "false",
    "WHATSAPP_DISPATCH_MODE": "disabled",
    "EMAIL_MAILBOX_SYNC_ENABLED": "false",
    "SPEEDAF_MCP_ENABLED": "false",
    "SPEEDAF_TRACK_QUERY_ENABLED": "false",
    "SPEEDAF_WORK_ORDER_CREATE_ENABLED": "false",
    "SPEEDAF_UPDATE_ADDRESS_ENABLED": "false",
    "SPEEDAF_CANCEL_ENABLED": "false",
    "SPEEDAF_VOICE_CALLBACK_ENABLED": "false",
    "OPERATIONS_DISPATCH_MODE": "disabled",
    "OPERATIONS_DISPATCH_ADAPTER": "disabled",
}


def _inside_repository(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return False
    return True


def _validate_database_url(database_url: str, *, allow_remote: bool) -> None:
    parsed = urlsplit(database_url)
    if parsed.scheme not in {"postgresql", "postgresql+psycopg", "postgresql+psycopg2"}:
        raise ValueError("postgresql_database_url_required")
    if not parsed.hostname or not parsed.username:
        raise ValueError("postgresql_database_authority_invalid")
    if parsed.query or parsed.fragment or "," in parsed.netloc.rsplit("@", 1)[-1]:
        raise ValueError("postgresql_database_url_unbounded")
    database = parsed.path.lstrip("/")
    if not database or "/" in database:
        raise ValueError("postgresql_database_name_invalid")
    if not any(marker in database.lower() for marker in DISPOSABLE_MARKERS):
        raise ValueError("disposable_database_name_marker_required")
    if parsed.hostname not in LOCAL_HOSTS and not allow_remote:
        raise ValueError("remote_database_requires_explicit_confirmation")


def _command(label: str, command: list[str], *, cwd: Path, env: dict[str, str]) -> dict[str, object]:
    started = datetime.now(timezone.utc)
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return {
        "label": label,
        "return_code": completed.returncode,
        "passed": completed.returncode == 0,
        "duration_seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 3),
        "output_included": False,
    }


def _current_revision(database_url: str) -> str | None:
    engine = create_engine(
        database_url,
        pool_size=1,
        max_overflow=0,
        pool_timeout=5,
        pool_pre_ping=True,
        future=True,
    )
    try:
        with engine.connect() as connection:
            rows = connection.execute(text("SELECT version_num FROM alembic_version ORDER BY version_num")).scalars().all()
            if len(rows) != 1:
                return None
            return str(rows[0])
    finally:
        engine.dispose()


def _write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_acceptance(
    *,
    database_url: str,
    evidence_dir: Path,
    source_sha: str,
    tree_sha: str,
    allow_remote: bool,
) -> dict[str, object]:
    _validate_database_url(database_url, allow_remote=allow_remote)
    directory = evidence_dir.expanduser().resolve()
    if _inside_repository(directory) or directory.is_symlink():
        raise ValueError("acceptance_evidence_directory_invalid")
    directory.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.update(FAIL_CLOSED_ENV)
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)

    migration_commands = [
        _command("alembic_upgrade", [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env),
    ]
    if migration_commands[-1]["passed"]:
        migration_commands.append(
            _command("alembic_downgrade", [sys.executable, "-m", "alembic", "downgrade", "-1"], cwd=BACKEND, env=env)
        )
    if migration_commands[-1]["passed"]:
        migration_commands.append(
            _command("alembic_reupgrade", [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env)
        )

    final_revision = _current_revision(database_url) if all(row["passed"] for row in migration_commands) else None
    migration_passed = len(migration_commands) == 3 and all(row["passed"] for row in migration_commands) and bool(final_revision)
    migration_payload: dict[str, object] = {
        "schema": "nexus.migration-rehearsal.v1",
        "status": "pass" if migration_passed else "fail",
        "source_sha": source_sha,
        "tree_sha": tree_sha,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "database_disposable": True,
        "upgrade_passed": bool(migration_commands[0]["passed"]),
        "downgrade_passed": len(migration_commands) > 1 and bool(migration_commands[1]["passed"]),
        "reupgrade_passed": len(migration_commands) > 2 and bool(migration_commands[2]["passed"]),
        "final_revision": final_revision,
        "commands": migration_commands,
        "sanitized": True,
        "contains_customer_data": False,
        "contains_secrets": False,
    }
    _write(directory / "migration-rehearsal.json", migration_payload)

    tests_result = None
    if migration_passed:
        tests_result = _command(
            "postgres_privacy_and_worker_tests",
            [sys.executable, "-m", "pytest", "-q", *POSTGRES_TESTS],
            cwd=ROOT,
            env=env,
        )
    tests_passed = bool(tests_result and tests_result["passed"])
    postgres_payload: dict[str, object] = {
        "schema": "nexus.postgres-qualification.v1",
        "status": "pass" if tests_passed else "fail",
        "source_sha": source_sha,
        "tree_sha": tree_sha,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "database_disposable": True,
        "tests_passed": tests_passed,
        "cross_scope_existence_safe": tests_passed,
        "lists_minimized": tests_passed,
        "sensitive_access_audited": tests_passed,
        "lease_fencing_passed": tests_passed,
        "test_command": tests_result,
        "sanitized": True,
        "contains_customer_data": False,
        "contains_secrets": False,
    }
    _write(directory / "postgres-qualification.json", postgres_payload)

    capacity_passed = False
    try:
        runtime = collect_postgresql_snapshot(database_url, top_queries=20)
        pools = [
            ProcessPool("web", 2, 5, 5),
            ProcessPool("outbound", 1, 2, 2),
            ProcessPool("background", 1, 3, 2),
            ProcessPool("webchat-ai", 1, 2, 1),
            ProcessPool("handoff", 1, 2, 1),
        ]
        budget = calculate_connection_budget(
            pools,
            database_max_connections=int(runtime["database_max_connections"]),
            reserved_connections=int(runtime["reserved_connections"]),
            maximum_budget_percent=70,
        )
        capacity_passed = bool(budget["within_budget"])
        capacity_payload = {
            "schema": "nexus.database-capacity-snapshot.v1",
            "status": "pass" if capacity_passed else "fail",
            "source_sha": source_sha,
            "tree_sha": tree_sha,
            "budget": budget,
            "runtime": runtime,
            "sanitized": True,
        }
    except Exception as exc:
        capacity_payload = {
            "schema": "nexus.database-capacity-snapshot.v1",
            "status": "fail",
            "source_sha": source_sha,
            "tree_sha": tree_sha,
            "error_type": type(exc).__name__,
            "sanitized": True,
        }
    _write(directory / "database-capacity.json", capacity_payload)

    passed = migration_passed and tests_passed and capacity_passed
    return {
        "schema": "nexus.postgres-acceptance-run.v1",
        "status": "pass" if passed else "fail",
        "source_sha": source_sha,
        "tree_sha": tree_sha,
        "migration_passed": migration_passed,
        "postgres_tests_passed": tests_passed,
        "database_capacity_passed": capacity_passed,
        "evidence_dir": str(directory),
        "database_url_included": False,
        "production_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""), required=False)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--tree-sha", required=True)
    parser.add_argument("--allow-remote-database", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("database URL is required")
    if args.allow_remote_database and os.getenv("NEXUS_ACCEPTANCE_REMOTE_DATABASE_CONFIRM") != "I_UNDERSTAND_DISPOSABLE_ONLY":
        raise SystemExit("remote disposable database requires explicit confirmation")
    payload = run_acceptance(
        database_url=args.database_url,
        evidence_dir=args.evidence_dir,
        source_sha=args.source_sha,
        tree_sha=args.tree_sha,
        allow_remote=args.allow_remote_database,
    )
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        output = args.output.expanduser().resolve()
        if _inside_repository(output):
            raise SystemExit("PostgreSQL acceptance output must remain outside candidate tree")
        _write(output, payload)
    print(rendered, end="")
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
