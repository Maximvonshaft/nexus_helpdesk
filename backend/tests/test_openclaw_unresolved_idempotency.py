from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/openclaw_unresolved_idempotency_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.db import Base  # noqa: E402
from app.models import OpenClawUnresolvedEvent  # noqa: E402
from app.services.openclaw_payload_hash import payload_hash  # noqa: E402
from app.services.openclaw_unresolved_store import (  # noqa: E402
    apply_openclaw_unresolved_store_patch,
    persist_unresolved_openclaw_event_by_hash,
)


@pytest.fixture()
def db_session(tmp_path):
    apply_openclaw_unresolved_store_patch()
    db_file = tmp_path / "openclaw_unresolved_idempotency.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False}, future=True)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _persist(db, payload: dict, *, source: str = "default", session_key: str = "session-1") -> OpenClawUnresolvedEvent:
    return persist_unresolved_openclaw_event_by_hash(
        db,
        source=source,
        session_key=session_key,
        event_type="message",
        recipient="recipient-1",
        source_chat_id="chat-1",
        preferred_reply_contact="recipient-1",
        payload=payload,
    )


def _active_count(db_session, *, source: str, session_key: str | None, payload_hash_value: str) -> int:
    from sqlalchemy import func

    normalized_session_key = session_key or ""
    return (
        db_session.query(OpenClawUnresolvedEvent)
        .filter(
            OpenClawUnresolvedEvent.source == source,
            func.coalesce(OpenClawUnresolvedEvent.session_key, "") == normalized_session_key,
            OpenClawUnresolvedEvent.payload_hash == payload_hash_value,
            OpenClawUnresolvedEvent.status.in_(["pending", "failed", "replaying"]),
        )
        .count()
    )


def test_payload_hash_is_key_order_independent():
    payload_a = {"type": "message", "message": {"body": "hello", "sessionKey": "s1"}, "route": {"b": 2, "a": 1}}
    payload_b = {"route": {"a": 1, "b": 2}, "message": {"sessionKey": "s1", "body": "hello"}, "type": "message"}

    assert payload_hash(payload_a) == payload_hash(payload_b)


def test_persist_unresolved_event_dedupes_same_semantic_payload_different_key_order(db_session):
    payload_a = {"type": "message", "message": {"body": "hello", "sessionKey": "s1"}, "route": {"b": 2, "a": 1}}
    payload_b = {"route": {"a": 1, "b": 2}, "message": {"sessionKey": "s1", "body": "hello"}, "type": "message"}

    first = _persist(db_session, payload_a)
    second = _persist(db_session, payload_b)
    db_session.commit()

    assert first.id == second.id
    assert db_session.query(OpenClawUnresolvedEvent).count() == 1
    row = db_session.query(OpenClawUnresolvedEvent).one()
    assert row.payload_hash == payload_hash(payload_a)
    assert row.payload_json


def test_persist_unresolved_event_creates_distinct_row_for_distinct_payload(db_session):
    first = _persist(db_session, {"type": "message", "message": {"body": "hello", "sessionKey": "s1"}})
    second = _persist(db_session, {"type": "message", "message": {"body": "different", "sessionKey": "s1"}})
    db_session.commit()

    assert first.id != second.id
    assert first.payload_hash != second.payload_hash
    assert db_session.query(OpenClawUnresolvedEvent).count() == 2


def test_resolved_unresolved_event_does_not_block_new_active_row(db_session):
    payload = {"type": "message", "message": {"body": "hello", "sessionKey": "s1"}}
    first = _persist(db_session, payload)
    first.status = "resolved"
    db_session.commit()

    second = _persist(db_session, payload)
    db_session.commit()

    assert first.id != second.id
    assert db_session.query(OpenClawUnresolvedEvent).count() == 2
    assert second.status == "pending"


def test_openclaw_bridge_live_function_is_rebound_to_hash_store(db_session):
    import app.services.openclaw_bridge as openclaw_bridge

    assert openclaw_bridge.persist_unresolved_openclaw_event is persist_unresolved_openclaw_event_by_hash
    live_source = inspect.getsource(openclaw_bridge.persist_unresolved_openclaw_event)
    assert "payload_hash" in live_source
    assert "OpenClawUnresolvedEvent.payload_json == payload_json" not in live_source

    payload_a = {"z": 1, "a": {"b": 2}}
    payload_b = {"a": {"b": 2}, "z": 1}

    first = openclaw_bridge.persist_unresolved_openclaw_event(
        db_session,
        source="default",
        session_key="session-live",
        event_type="message",
        recipient="recipient",
        source_chat_id="chat",
        preferred_reply_contact="recipient",
        payload=payload_a,
    )
    second = openclaw_bridge.persist_unresolved_openclaw_event(
        db_session,
        source="default",
        session_key="session-live",
        event_type="message",
        recipient="recipient",
        source_chat_id="chat",
        preferred_reply_contact="recipient",
        payload=payload_b,
    )

    assert first.id == second.id
    assert db_session.query(OpenClawUnresolvedEvent).count() == 1
    assert db_session.query(OpenClawUnresolvedEvent).one().payload_hash == payload_hash(payload_a)


def test_integrity_error_recovery_returns_existing_active_row_without_rolling_back_outer_work(db_session, monkeypatch):
    existing = _persist(db_session, {"type": "message", "message": {"body": "hello", "sessionKey": "s1"}})
    db_session.commit()

    pending_outer = _persist(db_session, {"type": "message", "message": {"body": "other", "sessionKey": "s2"}}, session_key="session-outer")

    import app.services.openclaw_unresolved_store as unresolved_store

    original_find = unresolved_store._find_existing_active_row
    calls = {"count": 0}

    def _race_find(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return None
        return original_find(*args, **kwargs)

    monkeypatch.setattr(unresolved_store, "_find_existing_active_row", _race_find)

    recovered = _persist(db_session, {"type": "message", "message": {"body": "hello", "sessionKey": "s1"}})
    db_session.commit()

    assert recovered.id == existing.id
    assert pending_outer.id is not None
    assert db_session.query(OpenClawUnresolvedEvent).filter(OpenClawUnresolvedEvent.id == pending_outer.id).one().session_key == "session-outer"
    assert _active_count(db_session, source="default", session_key="session-1", payload_hash_value=existing.payload_hash) == 1


def test_none_session_key_active_rows_are_deduped(db_session):
    payload = {"type": "message", "message": {"body": "hello"}}

    first = _persist(db_session, payload, session_key=None)
    second = _persist(db_session, payload, session_key=None)
    db_session.commit()

    assert first.id == second.id
    assert _active_count(db_session, source="default", session_key=None, payload_hash_value=first.payload_hash) == 1


def test_none_and_empty_session_key_share_same_active_bucket(db_session):
    payload = {"type": "message", "message": {"body": "hello"}}

    first = _persist(db_session, payload, session_key=None)
    second = _persist(db_session, payload, session_key="")
    db_session.commit()

    assert first.id == second.id
    assert _active_count(db_session, source="default", session_key=None, payload_hash_value=first.payload_hash) == 1
    assert _active_count(db_session, source="default", session_key="", payload_hash_value=first.payload_hash) == 1


def test_dropped_duplicate_terminal_row_does_not_block_new_none_session_key_row(db_session):
    payload = {"type": "message", "message": {"body": "hello"}}

    first = _persist(db_session, payload, session_key=None)
    first.status = "dropped_duplicate"
    db_session.commit()

    second = _persist(db_session, payload, session_key="")
    db_session.commit()

    assert first.id != second.id
    assert second.status == "pending"
    assert _active_count(db_session, source="default", session_key=None, payload_hash_value=second.payload_hash) == 1
