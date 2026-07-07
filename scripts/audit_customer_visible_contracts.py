#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

AI_ORIGINS = ("provider_runtime", "ai_runtime")
FORBIDDEN_ORIGINS = (
    "business_system",
    "tool_service",
    "knowledge_runtime",
    "safety_service",
    "handoff_notice",
)
HUMAN_BLOCKING_STATES = (
    "human_active",
    "human_review_required",
    "needs_human",
    "human_owned",
    "ready_to_reply",
)
V3_CONTRACT = "nexus.ai_reply.v3"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json_loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _rows(conn: Connection, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(text(sql), params).mappings().all()]


def _count(conn: Connection, sql: str, params: dict[str, Any]) -> int:
    row = conn.execute(text(sql), params).first()
    if row is None:
        return 0
    return int(row[0] or 0)


def _origin_params(prefix: str, values: tuple[str, ...]) -> tuple[str, dict[str, str]]:
    keys = []
    params: dict[str, str] = {}
    for idx, value in enumerate(values):
        key = f"{prefix}_{idx}"
        keys.append(f":{key}")
        params[key] = value
    return ", ".join(keys), params


def _payload_used_sources(payload: dict[str, Any]) -> list[Any]:
    grounding = payload.get("grounding") if isinstance(payload.get("grounding"), dict) else {}
    value = grounding.get("used_sources")
    return value if isinstance(value, list) else []


def _payload_unsupported_claims(payload: dict[str, Any]) -> list[Any]:
    grounding = payload.get("grounding") if isinstance(payload.get("grounding"), dict) else {}
    value = grounding.get("unsupported_claims")
    return value if isinstance(value, list) else []


def _payload_reply_type(payload: dict[str, Any], fallback: Any = None) -> str | None:
    reply = payload.get("reply") if isinstance(payload.get("reply"), dict) else {}
    return (reply.get("type") or fallback or None) if isinstance(reply, dict) else fallback


def audit_connection(conn: Connection, *, hours: int = 24) -> dict[str, Any]:
    window_hours = max(1, int(hours or 24))
    since = _now() - timedelta(hours=window_hours)
    ai_placeholders, ai_params = _origin_params("ai_origin", AI_ORIGINS)
    forbidden_placeholders, forbidden_params = _origin_params("forbidden_origin", FORBIDDEN_ORIGINS)
    human_state_placeholders, human_state_params = _origin_params("human_state", HUMAN_BLOCKING_STATES)
    params: dict[str, Any] = {"since": since, **ai_params, **forbidden_params, **human_state_params}

    checks: dict[str, int] = {}
    samples: dict[str, list[dict[str, Any]]] = {}

    ai_missing_sql = f"""
        select id, ticket_id, channel, origin, runtime_contract_version, created_at
        from ticket_outbound_messages
        where created_at >= :since
          and origin in ({ai_placeholders})
          and (
            runtime_trace_id is null
            or runtime_signature is null
            or runtime_contract_payload_json is null
            or runtime_contract_payload_sha256 is null
          )
        order by created_at desc
        limit 20
    """
    rows = _rows(conn, ai_missing_sql, params)
    checks["ai_missing_contract_fields"] = len(rows)
    samples["ai_missing_contract_fields"] = rows

    forbidden_sql = f"""
        select id, ticket_id, channel, origin, runtime_contract_version, runtime_reply_type, created_at
        from ticket_outbound_messages
        where created_at >= :since
          and origin in ({forbidden_placeholders})
        order by created_at desc
        limit 20
    """
    rows = _rows(conn, forbidden_sql, params)
    checks["forbidden_origin"] = len(rows)
    samples["forbidden_origin"] = rows

    originless_sql = """
        select id, ticket_id, channel, status, provider_status, created_at
        from ticket_outbound_messages
        where created_at >= :since
          and origin is null
          and runtime_contract_version is null
          and created_by is null
        order by created_at desc
        limit 20
    """
    rows = _rows(conn, originless_sql, params)
    checks["originless_legacy"] = len(rows)
    samples["originless_legacy"] = rows

    signed_body_mutation_sql = """
        select id, ticket_id, channel, origin, runtime_contract_version, failure_code, created_at
        from ticket_outbound_messages
        where created_at >= :since
          and failure_code = 'runtime_signed_body_mutation'
        order by created_at desc
        limit 20
    """
    rows = _rows(conn, signed_body_mutation_sql, params)
    checks["signed_body_mutation"] = len(rows)
    samples["signed_body_mutation"] = rows

    v3_sql = """
        select id, ticket_id, channel, origin, runtime_contract_version, runtime_reply_type,
               runtime_contract_payload_json, created_at
        from ticket_outbound_messages
        where created_at >= :since
          and runtime_contract_version = :v3_contract
        order by created_at desc
        limit 500
    """
    v3_rows = _rows(conn, v3_sql, {**params, "v3_contract": V3_CONTRACT})
    answer_without_sources: list[dict[str, Any]] = []
    unsupported_claims: list[dict[str, Any]] = []
    for row in v3_rows:
        payload = _json_loads(row.get("runtime_contract_payload_json"))
        reply_type = _payload_reply_type(payload, row.get("runtime_reply_type"))
        used_sources = _payload_used_sources(payload)
        claims = _payload_unsupported_claims(payload)
        if reply_type == "answer" and not used_sources:
            answer_without_sources.append(_sample(row, reason="missing_used_sources"))
        if reply_type in {"answer", "handoff_notice"} and claims:
            unsupported_claims.append(_sample(row, reason="unsupported_claims"))
    checks["v3_answer_without_sources"] = len(answer_without_sources)
    checks["v3_unsupported_claims"] = len(unsupported_claims)
    samples["v3_answer_without_sources"] = answer_without_sources[:20]
    samples["v3_unsupported_claims"] = unsupported_claims[:20]

    pollution_sql = """
        select id, ticket_no, source_channel, conversation_state, last_runtime_reply_at
        from tickets
        where last_runtime_reply_at >= :since
          and last_human_update is not null
          and last_ai_update is not null
          and last_human_update = last_ai_update
        order by last_runtime_reply_at desc
        limit 20
    """
    rows = _rows(conn, pollution_sql, params)
    checks["ai_human_field_pollution"] = len(rows)
    samples["ai_human_field_pollution"] = rows

    human_agent_sql = """
        select id, ticket_id, channel, origin, created_at
        from ticket_outbound_messages
        where created_at >= :since
          and origin = 'human_agent'
          and created_by is null
        order by created_at desc
        limit 20
    """
    rows = _rows(conn, human_agent_sql, params)
    checks["human_agent_missing_created_by"] = len(rows)
    samples["human_agent_missing_created_by"] = rows

    ai_during_human_sql = f"""
        select m.id, m.ticket_id, m.channel, m.origin, m.runtime_reply_type, t.conversation_state, m.created_at
        from ticket_outbound_messages m
        join tickets t on t.id = m.ticket_id
        where m.created_at >= :since
          and m.origin in ({ai_placeholders})
          and m.runtime_reply_type = 'answer'
          and t.conversation_state in ({human_state_placeholders})
        order by m.created_at desc
        limit 20
    """
    rows = _rows(conn, ai_during_human_sql, params)
    checks["ai_answer_during_human_state"] = len(rows)
    samples["ai_answer_during_human_state"] = rows

    ok = all(value == 0 for value in checks.values())
    summary: dict[str, Any] = {
        "ok": ok,
        "window_hours": window_hours,
        "checks": checks,
    }
    risky_samples = {key: value for key, value in samples.items() if value}
    if risky_samples:
        summary["samples"] = risky_samples
    return summary


def _sample(row: dict[str, Any], *, reason: str) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "ticket_id": row.get("ticket_id"),
        "channel": row.get("channel"),
        "origin": row.get("origin"),
        "runtime_contract_version": row.get("runtime_contract_version"),
        "runtime_reply_type": row.get("runtime_reply_type"),
        "created_at": row.get("created_at"),
        "reason": reason,
    }


def _database_url() -> str:
    try:
        from app.settings import get_settings  # type: ignore

        return get_settings().database_url
    except Exception:
        value = os.getenv("DATABASE_URL")
        if not value:
            raise RuntimeError("DATABASE_URL is not set and app settings could not be loaded")
        return value


def _print_text(summary: dict[str, Any]) -> None:
    status = "OK" if summary.get("ok") else "RISK"
    print(f"customer visible contract audit: {status}")
    print(f"window_hours: {summary.get('window_hours')}")
    checks = summary.get("checks") or {}
    for key in sorted(checks):
        print(f"{key}: {checks[key]}")
    if summary.get("samples"):
        print("samples:")
        print(json.dumps(summary["samples"], ensure_ascii=False, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit customer-visible outbound contract invariants.")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--fail-on-risk", action="store_true", help="Kept for compatibility; risk always exits 2.")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    args = parser.parse_args(argv)

    engine = create_engine(_database_url(), future=True)
    try:
        with engine.connect() as conn:
            summary = audit_connection(conn, hours=args.hours)
    finally:
        engine.dispose()

    if args.format == "json":
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    else:
        _print_text(summary)
    return 0 if summary.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
