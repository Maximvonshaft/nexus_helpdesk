#!/usr/bin/env python3
"""Prove the isolated RC journey produced no external-effect execution records."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# This helper is executed by absolute path from /app/scripts while the app
# package is installed as source under /app/backend. Bootstrap that exact root
# before importing the application boundary.
_BACKEND_ROOT = Path(__file__).resolve().parents[2] / "backend"
if not _BACKEND_ROOT.is_dir():
    raise SystemExit("RC_SIDE_EFFECT_PROOF_FAILED reason=backend_root_missing")
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
ZERO_ROW_TABLES = (
    "provider_runtime_audit_logs",
    "provider_auth_sessions",
    "provider_credentials",
    "outbound_messages",
    "operations_dispatch_outbox",
)

FORBIDDEN_SECRET_ENV = (
    "PRIVATE_AI_RUNTIME_TOKEN",
    "OPENAI_API_KEY",
    "WHATSAPP_ACCESS_TOKEN",
    "SMTP_PASSWORD",
    "SPEEDAF_API_KEY",
)


def main() -> int:
    bad_env = {
        key: {"actual": os.getenv(key), "expected": expected}
        for key, expected in EXPECTED_ENV.items()
        if (os.getenv(key) or "").strip().lower() != expected
    }
    present_secrets = sorted(key for key in FORBIDDEN_SECRET_ENV if os.getenv(key))
    if bad_env or present_secrets:
        raise SystemExit("unsafe RC external-effect environment")

    register_all_models()
    db = SessionLocal()
    try:
        table_names = set(inspect(db.get_bind()).get_table_names(schema="public"))
        missing = sorted(set(ZERO_ROW_TABLES) - table_names)
        if missing:
            raise SystemExit("missing external-effect tables: " + ", ".join(missing))
        counts = {
            table: int(db.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar_one())
            for table in ZERO_ROW_TABLES
        }
    finally:
        db.close()

    nonzero = {table: count for table, count in counts.items() if count != 0}
    if nonzero:
        raise SystemExit("external-effect execution records detected")

    print(
        json.dumps(
            {
                "schema": "nexus.osr.rc-test-side-effect-safety.v2",
                "status": "pass",
                "effective_controls": {
                    "provider_kill_switch": True,
                    "provider_canary_percent": 0,
                    "private_runtime_enabled": False,
                    "outbound_dispatch_enabled": False,
                    "whatsapp_enabled": False,
                    "speedaf_write_enabled": False,
                    "operations_dispatch_enabled": False,
                },
                "forbidden_secret_env_present": [],
                "execution_row_counts": counts,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
