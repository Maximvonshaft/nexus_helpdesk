from __future__ import annotations

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
