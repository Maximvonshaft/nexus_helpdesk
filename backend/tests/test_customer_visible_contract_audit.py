import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.audit_customer_visible_contracts import audit_connection  # noqa: E402


def _setup(conn):
    conn.execute(text("""
        create table ticket_outbound_messages (
            id integer primary key,
            ticket_id integer,
            channel text,
            status text,
            body text,
            origin text,
            runtime_trace_id text,
            runtime_contract_version text,
            runtime_signature text,
            runtime_contract_payload_json text,
            runtime_contract_payload_sha256 text,
            runtime_reply_type text,
            safety_status text,
            provider_status text,
            created_by integer,
            failure_code text,
            created_at timestamp
        )
    """))
    conn.execute(text("""
        create table tickets (
            id integer primary key,
            ticket_no text,
            source_channel text,
            conversation_state text,
            last_runtime_reply_at timestamp,
            last_human_update text,
            last_ai_update text
        )
    """))


def _recent():
    return datetime.now(timezone.utc) - timedelta(minutes=5)


def _engine():
    return create_engine("sqlite:///:memory:", future=True)


def test_audit_customer_visible_contracts_reports_clean_db():
    engine = _engine()
    with engine.begin() as conn:
        _setup(conn)
        summary = audit_connection(conn, hours=24)
    assert summary["ok"] is True
    assert all(value == 0 for value in summary["checks"].values())


def test_audit_customer_visible_contracts_flags_forbidden_origin():
    engine = _engine()
    with engine.begin() as conn:
        _setup(conn)
        conn.execute(
            text("insert into ticket_outbound_messages (id, ticket_id, channel, status, origin, created_at) values (1, 10, 'web_chat', 'sent', 'handoff_notice', :created_at)"),
            {"created_at": _recent()},
        )
        summary = audit_connection(conn, hours=24)
    assert summary["ok"] is False
    assert summary["checks"]["forbidden_origin"] == 1


def test_audit_customer_visible_contracts_flags_v3_answer_without_sources():
    payload = json.dumps({"reply": {"type": "answer", "text": "ok"}, "grounding": {"used_sources": [], "unsupported_claims": [], "conflicts": []}, "channel": "webchat"})
    engine = _engine()
    with engine.begin() as conn:
        _setup(conn)
        conn.execute(
            text("""
                insert into ticket_outbound_messages
                  (id, ticket_id, channel, status, origin, runtime_trace_id, runtime_contract_version,
                   runtime_signature, runtime_contract_payload_json, runtime_contract_payload_sha256,
                   runtime_reply_type, safety_status, created_at)
                values
                  (1, 10, 'web_chat', 'sent', 'provider_runtime', 'rt-1',
                   'nexus.ai_reply.v3', 'sig', :payload, 'hash', 'answer', 'passed', :created_at)
            """),
            {"payload": payload, "created_at": _recent()},
        )
        summary = audit_connection(conn, hours=24)
    assert summary["ok"] is False
    assert summary["checks"]["v3_answer_without_sources"] == 1


def test_audit_customer_visible_contracts_flags_originless_legacy():
    engine = _engine()
    with engine.begin() as conn:
        _setup(conn)
        conn.execute(
            text("insert into ticket_outbound_messages (id, ticket_id, channel, status, origin, runtime_contract_version, created_by, created_at) values (1, 10, 'whatsapp', 'pending', null, null, null, :created_at)"),
            {"created_at": _recent()},
        )
        summary = audit_connection(conn, hours=24)
    assert summary["ok"] is False
    assert summary["checks"]["originless_legacy"] == 1
