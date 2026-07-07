#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
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

HIGH_RISK_TERMS = (
    "refund",
    "compensation",
    "customs",
    "tax",
    "delivery time",
    "tracking status",
    "赔付",
    "赔偿",
    "退款",
    "清关",
    "税",
    "时效",
    "物流状态",
)
AUTHORITATIVE_MARKERS = ("tool:", "tool.", "official_policy", "authority:official_policy")
KB_MARKERS = ("kb:", "knowledge:", "knowledge.")
GLOBAL = "GLOBAL"


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


def _nested_get(payload: dict[str, Any], *paths: str) -> Any:
    for path in paths:
        cur: Any = payload
        found = True
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                found = False
                break
        if found and cur not in (None, ""):
            return cur
    return None


def _reply(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("reply")
    return value if isinstance(value, dict) else {}


def _grounding(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("grounding")
    return value if isinstance(value, dict) else {}


def _used_sources(payload: dict[str, Any]) -> list[str]:
    value = _grounding(payload).get("used_sources")
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item or "").strip()]


def _authority(payload: dict[str, Any], sources: list[str]) -> str:
    authority = _nested_get(payload, "authority_level", "grounding.authority_level", "risk.authority_level", "runtime_trace.authority_level")
    if authority:
        return str(authority)
    joined = " ".join(sources).lower()
    if "official_policy" in joined:
        return "official_policy"
    if "policy" in joined:
        return "policy"
    if any(marker in joined for marker in ("tool:", "tool.")):
        return "tool"
    if any(marker in joined for marker in KB_MARKERS):
        return "kb"
    return "unknown"


def _source_type(payload: dict[str, Any], sources: list[str]) -> str:
    value = _nested_get(payload, "source_type", "grounding.source_type", "runtime_trace.source_type")
    if value:
        return str(value)
    joined = " ".join(sources).lower()
    if any(marker in joined for marker in ("tool:", "tool.")):
        return "tool"
    if any(marker in joined for marker in KB_MARKERS):
        return "kb"
    if sources:
        return "source"
    return "none"


def _effective_country(payload: dict[str, Any]) -> str:
    value = _nested_get(
        payload,
        "effective_country",
        "runtime_context.effective_country",
        "runtime_trace.effective_country",
        "rag_trace.effective_country",
        "rag_trace.filters.country_scope",
        "filters.country_scope",
    )
    return str(value or GLOBAL).upper()


def _country_source(payload: dict[str, Any], effective_country: str) -> str:
    value = _nested_get(payload, "country_source", "runtime_context.country_source", "runtime_trace.country_source", "rag_trace.country_source")
    if value:
        return str(value)
    return "default/global" if effective_country == GLOBAL else "runtime_context"


def _channel(payload: dict[str, Any], fallback: Any) -> str:
    value = _nested_get(payload, "channel", "runtime_context.channel", "runtime_trace.channel")
    return str(value or fallback or "unknown")


def _language(payload: dict[str, Any]) -> str:
    return str(_nested_get(payload, "language", "runtime_context.language", "runtime_trace.language") or "unknown")


def _intent(payload: dict[str, Any]) -> str:
    return str(_nested_get(payload, "intent", "runtime_trace.intent") or "unknown")


def _reply_type(payload: dict[str, Any], fallback: Any = None) -> str:
    return str(_reply(payload).get("type") or fallback or "unknown")


def _reply_text(payload: dict[str, Any]) -> str:
    return str(_reply(payload).get("text") or payload.get("customer_reply") or "")


def _high_risk(text_value: str, intent: str) -> bool:
    lowered = f"{text_value} {intent}".lower()
    return any(term.lower() in lowered for term in HIGH_RISK_TERMS)


def _has_authoritative_source(sources: list[str], authority: str) -> bool:
    if authority in {"tool", "official_policy"}:
        return True
    joined = " ".join(sources).lower()
    return any(marker in joined for marker in AUTHORITATIVE_MARKERS)


def audit_connection(conn: Connection, *, hours: int = 24) -> dict[str, Any]:
    window_hours = max(1, int(hours or 24))
    since = _now() - timedelta(hours=window_hours)
    rows = _rows(
        conn,
        """
        select id, ticket_id, channel, origin, runtime_contract_version, runtime_reply_type,
               runtime_contract_payload_json, created_at
        from ticket_outbound_messages
        where created_at >= :since
          and (
            runtime_contract_payload_json is not null
            or origin in ('provider_runtime', 'ai_runtime')
            or runtime_contract_version is not null
          )
        order by created_at desc
        limit 2000
        """,
        {"since": since},
    )

    distribution: Counter[tuple[str, str]] = Counter()
    no_answer_groups: Counter[tuple[str, str, str]] = Counter()
    hit_groups: Counter[tuple[str, str, str, str]] = Counter()
    country_specific_rows = 0
    country_specific_hits = 0
    country_specific_global_fallback_hits = 0
    country_specific_no_hits = 0
    global_fallbacks = 0
    high_risk_no_source_answers: list[dict[str, Any]] = []
    handoff_after_kb_hit: list[dict[str, Any]] = []
    no_hit_queries: list[dict[str, Any]] = []

    for row in rows:
        payload = _json_loads(row.get("runtime_contract_payload_json"))
        country = _effective_country(payload)
        country_source = _country_source(payload, country)
        channel = _channel(payload, row.get("channel"))
        language = _language(payload)
        intent = _intent(payload)
        reply_type = _reply_type(payload, row.get("runtime_reply_type"))
        sources = _used_sources(payload)
        authority = _authority(payload, sources)
        source_type = _source_type(payload, sources)
        text_value = _reply_text(payload)

        distribution[(country, country_source)] += 1
        if country == GLOBAL or country_source in {"default", "default/global", "global"}:
            global_fallbacks += 1

        if country != GLOBAL:
            country_specific_rows += 1
            if sources:
                if any(GLOBAL.lower() in source.lower() for source in sources):
                    country_specific_global_fallback_hits += 1
                else:
                    country_specific_hits += 1
            else:
                country_specific_no_hits += 1

        if sources:
            hit_groups[(channel, country, authority, source_type)] += 1
        else:
            no_answer_groups[(channel, country, language)] += 1
            no_hit_queries.append({"outbound_id": row.get("id"), "ticket_id": row.get("ticket_id"), "channel": channel, "effective_country": country, "intent": intent, "language": language, "reply_type": reply_type})

        if reply_type == "answer" and _high_risk(text_value, intent) and not _has_authoritative_source(sources, authority):
            high_risk_no_source_answers.append({"outbound_id": row.get("id"), "ticket_id": row.get("ticket_id"), "channel": channel, "effective_country": country, "intent": intent, "authority_level": authority, "source_count": len(sources)})

        if reply_type == "handoff_notice" and sources:
            handoff_after_kb_hit.append({"outbound_id": row.get("id"), "ticket_id": row.get("ticket_id"), "effective_country": country, "authority_level": authority, "handoff_reason": _nested_get(payload, "handoff_reason", "runtime_trace.handoff_reason") or "unknown"})

    total = len(rows)
    global_fallback_rate = round(global_fallbacks / total, 4) if total else 0.0
    country_specific_denominator = country_specific_hits + country_specific_global_fallback_hits + country_specific_no_hits
    country_specific_hit_rate = round(country_specific_hits / country_specific_denominator, 4) if country_specific_denominator else 0.0

    return {
        "window_hours": window_hours,
        "effective_country_distribution": [
            {"effective_country": country, "country_source": source, "count": count}
            for (country, source), count in sorted(distribution.items())
        ],
        "global_fallback_rate": global_fallback_rate,
        "country_specific_hit_rate": country_specific_hit_rate,
        "country_specific_detail": {
            "country_specific_rows": country_specific_rows,
            "country_specific_hits": country_specific_hits,
            "global_fallback_hits": country_specific_global_fallback_hits,
            "no_hit": country_specific_no_hits,
        },
        "knowledge_hits": [
            {"channel": channel, "effective_country": country, "authority_level": authority, "source_type": source_type, "count": count}
            for (channel, country, authority, source_type), count in sorted(hit_groups.items())
        ],
        "no_answer_groups": [
            {"channel": channel, "effective_country": country, "language": language, "count": count}
            for (channel, country, language), count in sorted(no_answer_groups.items())
        ],
        "high_risk_no_source_answers": high_risk_no_source_answers[:50],
        "handoff_after_kb_hit": handoff_after_kb_hit[:50],
        "no_hit_queries": no_hit_queries[:50],
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit Knowledge Runtime effective_country and grounding quality.")
    parser.add_argument("--hours", type=int, default=24)
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
        print(f"knowledge runtime quality audit window_hours={summary['window_hours']}")
        print(f"global_fallback_rate={summary['global_fallback_rate']}")
        print(f"country_specific_hit_rate={summary['country_specific_hit_rate']}")
        print(f"high_risk_no_source_answers={len(summary['high_risk_no_source_answers'])}")
        print(f"handoff_after_kb_hit={len(summary['handoff_after_kb_hit'])}")
    return 2 if summary["high_risk_no_source_answers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
