#!/usr/bin/env python3
"""Prove the isolated RC journey produced no external-effect execution records."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# Stable bounded exit codes consumed by the RC failure summary.
EXIT_BACKEND_ROOT_MISSING = 20
EXIT_UNSAFE_ENVIRONMENT = 21
EXIT_DATABASE_INSPECTION = 22
EXIT_MISSING_MULTIPLE_TABLES = 23
EXIT_EXECUTION_RECORDS = 24
MISSING_TABLE_EXIT_CODES = {
    "provider_runtime_audit_logs": 31,
    "provider_auth_sessions": 32,
    "provider_credentials": 33,
    "ticket_outbound_messages": 34,
    "operations_dispatch_outbox": 35,
}

# This helper is executed by absolute path from /app/scripts while the app
# package is installed as source under /app/backend. Bootstrap that exact root
# before importing the application boundary.
_BACKEND_ROOT = Path(__file__).resolve().parents[2] / "backend"
if not _BACKEND_ROOT.is_dir():
    print(json.dumps({
        "schema": "nexus.osr.rc-test-side-effect-safety.v2",
        "status": "failed",
        "reason_code": "backend_root_missing",
    }, sort_keys=True))
    raise SystemExit(EXIT_BACKEND_ROOT_MISSING)
_backend_text = str(_BACKEND_ROOT)
if _backend_text not in sys.path:
    sys.path.insert(0, _backend_text)

from sqlalchemy import inspect, text

from app.db import SessionLocal
from app.model_registry import register_all_models


EXPECTED_ENV = {
    "PROVIDER_RUNTIME_CANARY_PERCENT": "0",
    "PROVIDER_RUNTIME_KILL_SWITCH": "true",
    "PRIVATE_AI_RUNTIME_ENABLED": "false",
    "ENABLE_OUTBOUND_DISPATCH": "false",
    "OUTBOUND_PROVIDER": "disabled",
    "OUTBOUND_EMAIL_PRODUCTION_PILOT_ENABLED": "false",
    "WHATSAPP_NATIVE_ENABLED": "false",
    "WHATSAPP_DISPATCH_MODE": "disabled",
    "EMAIL_MAILBOX_SYNC_ENABLED": "false",
    "SPEEDAF_WORK_ORDER_CREATE_ENABLED": "false",
    "SPEEDAF_UPDATE_ADDRESS_ENABLED": "false",
    "SPEEDAF_CANCEL_ENABLED": "false",
    "OPERATIONS_DISPATCH_MODE": "disabled",
    "OPERATIONS_DISPATCH_ADAPTER": "disabled",
}

# These are durable execution/transport surfaces, not ordinary synthetic WebChat
# data. A clean isolated RC journey must leave each at zero rows.
ZERO_ROW_TABLES = tuple(MISSING_TABLE_EXIT_CODES)

FORBIDDEN_SECRET_ENV = (
    "PRIVATE_AI_RUNTIME_TOKEN",
    "OPENAI_API_KEY",
    "WHATSAPP_ACCESS_TOKEN",
    "SMTP_PASSWORD",
    "SPEEDAF_API_KEY",
)


def _emit(*, status: str, reason_code: str | None = None, **details: Any) -> None:
    payload: dict[str, Any] = {
        "schema": "nexus.osr.rc-test-side-effect-safety.v2",
        "status": status,
    }
    if reason_code:
        payload["reason_code"] = reason_code
    payload.update(details)
    print(json.dumps(payload, indent=2, sort_keys=True))


def main() -> int:
    bad_control_keys = sorted(
        key
        for key, expected in EXPECTED_ENV.items()
        if (os.getenv(key) or "").strip().lower() != expected
    )
    present_secret_names = sorted(key for key in FORBIDDEN_SECRET_ENV if os.getenv(key))
    if bad_control_keys or present_secret_names:
        _emit(
            status="failed",
            reason_code="unsafe_environment_controls",
            bad_control_keys=bad_control_keys,
            forbidden_secret_names=present_secret_names,
        )
        return EXIT_UNSAFE_ENVIRONMENT

    try:
        register_all_models()
        db = SessionLocal()
        try:
            table_names = set(inspect(db.get_bind()).get_table_names(schema="public"))
            missing = sorted(set(ZERO_ROW_TABLES) - table_names)
            if missing:
                _emit(
                    status="failed",
                    reason_code="missing_execution_tables",
                    missing_execution_tables=missing,
                )
                if len(missing) == 1:
                    return MISSING_TABLE_EXIT_CODES[missing[0]]
                return EXIT_MISSING_MULTIPLE_TABLES
            counts = {
                table: int(db.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar_one())
                for table in ZERO_ROW_TABLES
            }
        finally:
            db.close()
    except Exception:
        _emit(status="failed", reason_code="database_inspection_failed")
        return EXIT_DATABASE_INSPECTION

    nonzero = {table: count for table, count in counts.items() if count != 0}
    if nonzero:
        _emit(
            status="failed",
            reason_code="execution_records_detected",
            execution_row_counts=counts,
            affected_execution_tables=sorted(nonzero),
        )
        return EXIT_EXECUTION_RECORDS

    _emit(
        status="pass",
        effective_controls={
            "provider_kill_switch": True,
            "provider_canary_percent": 0,
            "private_runtime_enabled": False,
            "outbound_dispatch_enabled": False,
            "whatsapp_enabled": False,
            "speedaf_write_enabled": False,
            "operations_dispatch_enabled": False,
        },
        forbidden_secret_env_present=[],
        execution_row_counts=counts,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
