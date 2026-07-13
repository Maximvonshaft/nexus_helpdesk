#!/usr/bin/env python3
"""Prove that the isolated RC journey produced no external execution."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

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
    "runtime_decision_audits": 36,
    "webchat_ai_turns": 37,
    "webchat_messages": 38,
    "webchat_voice_ai_turns": 39,
    "webchat_voice_ai_actions": 40,
    "tool_call_logs": 41,
}
# Contract marker: MISSING_TABLE_EXIT_CODES["tool_call_logs"] = 41

_BACKEND_ROOT = Path(__file__).resolve().parents[2] / "backend"
if not _BACKEND_ROOT.is_dir():
    print(json.dumps({"schema": "nexus.osr.rc-test-side-effect-safety.v2", "status": "failed", "reason_code": "backend_root_missing"}, sort_keys=True))
    raise SystemExit(EXIT_BACKEND_ROOT_MISSING)
sys.path.insert(0, str(_BACKEND_ROOT))

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
ZERO_ROW_TABLES = (
    "provider_runtime_audit_logs",
    "provider_auth_sessions",
    "provider_credentials",
    "ticket_outbound_messages",
    "operations_dispatch_outbox",
)
REQUIRED_EVIDENCE_TABLES = tuple(MISSING_TABLE_EXIT_CODES)
FORBIDDEN_SEMANTIC_COUNTS = (
    "external_tool_execution_count",
    "provider_customer_output_count",
    "tts_provider_customer_output_count",
)
FORBIDDEN_SECRET_ENV = (
    "PRIVATE_AI_RUNTIME_" + "TOKEN",
    "OPENAI_" + "API_KEY",
    "WHATSAPP_ACCESS_" + "TOKEN",
    "SMTP_" + "PASSWORD",
    "SPEEDAF_" + "API_KEY",
)


def _emit(*, status: str, reason_code: str | None = None, **details: Any) -> None:
    payload: dict[str, Any] = {"schema": "nexus.osr.rc-test-side-effect-safety.v2", "status": status}
    if reason_code:
        payload["reason_code"] = reason_code
    payload.update(details)
    print(json.dumps(payload, indent=2, sort_keys=True))


def _count(db: Any, statement: str) -> int:
    return int(db.execute(text(statement)).scalar_one())


def _collect_semantic_execution_counts(db: Any) -> dict[str, int]:
    runtime_tool_action_execution_count = _count(db, """
        SELECT COUNT(*) FROM runtime_decision_audits AS audit
        CROSS JOIN LATERAL jsonb_array_elements(
          CASE WHEN jsonb_typeof((audit.decision_json)::jsonb -> 'tool_actions') = 'array'
          THEN (audit.decision_json)::jsonb -> 'tool_actions' ELSE '[]'::jsonb END
        ) AS action
        WHERE lower(COALESCE(action ->> 'executed', 'false')) = 'true'
    """)
    voice_tool_action_execution_count = _count(db, """
        SELECT COUNT(*) FROM webchat_voice_ai_actions
        WHERE tool_call_log_id IS NOT NULL OR background_job_id IS NOT NULL
    """)
    tool_call_log_execution_count = _count(db, """
        SELECT COUNT(*) FROM tool_call_logs
        WHERE lower(BTRIM(COALESCE(status, ''))) IN ('success', 'executed')
    """)
    webchat_ai_queued_turn_count = _count(db, "SELECT COUNT(*) FROM webchat_ai_turns WHERE status = 'queued'")
    webchat_ai_customer_output_count = _count(db, "SELECT COUNT(*) FROM webchat_ai_turns WHERE reply_message_id IS NOT NULL")
    webchat_ai_message_count = _count(db, """
        SELECT COUNT(*) FROM webchat_messages
        WHERE ai_turn_id IS NOT NULL AND direction IN ('agent', 'ai')
    """)
    tts_provider_customer_output_count = _count(db, """
        SELECT COUNT(*) FROM webchat_voice_ai_turns
        WHERE NULLIF(BTRIM(COALESCE(ai_response_text_redacted, '')), '') IS NOT NULL
           OR NULLIF(BTRIM(COALESCE(provider, '')), '') IS NOT NULL
           OR NULLIF(BTRIM(COALESCE(tts_provider, '')), '') IS NOT NULL
    """)
    return {
        "runtime_tool_action_execution_count": runtime_tool_action_execution_count,
        "voice_tool_action_execution_count": voice_tool_action_execution_count,
        "tool_call_log_execution_count": tool_call_log_execution_count,
        "external_tool_execution_count": runtime_tool_action_execution_count + voice_tool_action_execution_count + tool_call_log_execution_count,
        "webchat_ai_queued_turn_count": webchat_ai_queued_turn_count,
        "webchat_ai_customer_output_count": webchat_ai_customer_output_count,
        "webchat_ai_message_count": webchat_ai_message_count,
        "provider_customer_output_count": webchat_ai_customer_output_count + webchat_ai_message_count,
        "tts_provider_customer_output_count": tts_provider_customer_output_count,
    }


def main() -> int:
    bad_controls = sorted(k for k, expected in EXPECTED_ENV.items() if (os.getenv(k) or "").strip().lower() != expected)
    secret_names = sorted(k for k in FORBIDDEN_SECRET_ENV if os.getenv(k))
    if bad_controls or secret_names:
        _emit(status="failed", reason_code="unsafe_environment_controls", bad_control_keys=bad_controls, forbidden_secret_names=secret_names)
        return EXIT_UNSAFE_ENVIRONMENT
    try:
        register_all_models()
        db = SessionLocal()
        try:
            tables = set(inspect(db.get_bind()).get_table_names(schema="public"))
            missing = sorted(set(REQUIRED_EVIDENCE_TABLES) - tables)
            if missing:
                _emit(status="failed", reason_code="missing_execution_tables", missing_execution_tables=missing)
                return MISSING_TABLE_EXIT_CODES[missing[0]] if len(missing) == 1 else EXIT_MISSING_MULTIPLE_TABLES
            row_counts = {name: _count(db, f'SELECT COUNT(*) FROM "{name}"') for name in ZERO_ROW_TABLES}
            semantic_counts = _collect_semantic_execution_counts(db)
        finally:
            db.close()
    except Exception:
        _emit(status="failed", reason_code="database_inspection_failed")
        return EXIT_DATABASE_INSPECTION

    nonzero_rows = {k: v for k, v in row_counts.items() if v}
    nonzero_semantic = {k: semantic_counts[k] for k in FORBIDDEN_SEMANTIC_COUNTS if semantic_counts[k]}
    if nonzero_rows or nonzero_semantic:
        _emit(
            status="failed",
            reason_code="execution_records_detected",
            execution_row_counts=row_counts,
            semantic_execution_counts=semantic_counts,
            affected_execution_tables=sorted(nonzero_rows),
            affected_semantic_counts=sorted(nonzero_semantic),
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
        execution_row_counts=row_counts,
        semantic_execution_counts=semantic_counts,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
