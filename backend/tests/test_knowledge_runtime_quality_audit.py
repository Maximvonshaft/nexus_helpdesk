import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.audit_knowledge_runtime_quality import audit_connection  # noqa: E402


def _engine():
    return create_engine("sqlite:///:memory:", future=True)


def _recent():
    return datetime.now(timezone.utc) - timedelta(minutes=5)


def _setup(conn):
    conn.execute(text("""
        create table ticket_outbound_messages (
            id integer primary key,
            ticket_id integer,
            channel text,
            origin text,
            runtime_contract_version text,
            runtime_reply_type text,
            runtime_contract_payload_json text,
            created_at timestamp
        )
    """))


def _insert_payload(conn, payload, *, row_id=1, channel="web_chat"):
    conn.execute(
        text("""
            insert into ticket_outbound_messages
              (id, ticket_id, channel, origin, runtime_contract_version, runtime_reply_type, runtime_contract_payload_json, created_at)
            values
              (:id, 10, :channel, 'provider_runtime', 'nexus.ai_reply.v3', :reply_type, :payload, :created_at)
        """),
        {
            "id": row_id,
            "channel": channel,
            "reply_type": payload.get("reply", {}).get("type", "answer"),
            "payload": json.dumps(payload),
            "created_at": _recent(),
        },
    )


def test_knowledge_quality_audit_counts_global_fallback():
    payload = {
        "reply": {"type": "answer", "text": "ok"},
        "grounding": {"used_sources": ["knowledge:GLOBAL:faq"], "unsupported_claims": [], "conflicts": []},
        "effective_country": "GLOBAL",
        "country_source": "default/global",
        "channel": "webchat",
    }
    engine = _engine()
    with engine.begin() as conn:
        _setup(conn)
        _insert_payload(conn, payload)
        summary = audit_connection(conn, hours=24)
    assert summary["global_fallback_rate"] == 1.0
    assert summary["effective_country_distribution"][0]["effective_country"] == "GLOBAL"


def test_knowledge_quality_audit_flags_high_risk_no_source():
    payload = {
        "reply": {"type": "answer", "text": "refund status"},
        "intent": "refund",
        "grounding": {"used_sources": [], "unsupported_claims": [], "conflicts": []},
        "effective_country": "US",
        "country_source": "runtime_context",
        "channel": "webchat",
    }
    engine = _engine()
    with engine.begin() as conn:
        _setup(conn)
        _insert_payload(conn, payload)
        summary = audit_connection(conn, hours=24)
    assert len(summary["high_risk_no_source_answers"]) == 1
    assert summary["high_risk_no_source_answers"][0]["effective_country"] == "US"


def test_knowledge_quality_audit_groups_by_effective_country():
    engine = _engine()
    with engine.begin() as conn:
        _setup(conn)
        _insert_payload(conn, {"reply": {"type": "answer", "text": "ok"}, "grounding": {"used_sources": ["knowledge:US:returns"], "unsupported_claims": [], "conflicts": []}, "effective_country": "US", "country_source": "runtime_context", "channel": "webchat"}, row_id=1)
        _insert_payload(conn, {"reply": {"type": "answer", "text": "ok"}, "grounding": {"used_sources": ["knowledge:MX:returns"], "unsupported_claims": [], "conflicts": []}, "effective_country": "MX", "country_source": "runtime_context", "channel": "webchat"}, row_id=2)
        summary = audit_connection(conn, hours=24)
    countries = {row["effective_country"] for row in summary["effective_country_distribution"]}
    assert countries == {"US", "MX"}
