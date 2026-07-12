from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

from app.services import external_channel_bridge as bridge

ROOT = Path(__file__).resolve().parents[2]
ACTIVE_STATUSES = {"pending", "failed", "replaying"}


class _Query:
    def __init__(self, row):
        self.row = row

    def filter(self, *_args):
        return self

    def order_by(self, *_args):
        return self

    def first(self):
        return self.row


class _Nested:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeDB:
    def __init__(self, row=None):
        self.row = row
        self.added = []
        self.flush_count = 0
        self.nested_count = 0

    def query(self, _model):
        return _Query(self.row)

    def add(self, row):
        self.added.append(row)

    def flush(self):
        self.flush_count += 1

    def begin_nested(self):
        self.nested_count += 1
        return _Nested()


def test_bridge_owns_the_public_persistence_signature() -> None:
    signature = inspect.signature(bridge.persist_unresolved_external_channel_event)
    assert list(signature.parameters) == ["db", "event", "source", "session_key", "error"]
    assert signature.parameters["event"].kind is inspect.Parameter.KEYWORD_ONLY


def test_payload_hash_is_canonical_and_order_independent() -> None:
    left = {"type": "message", "route": {"recipient": "+1", "threadId": "t"}, "value": 7}
    right = {"value": 7, "route": {"threadId": "t", "recipient": "+1"}, "type": "message"}
    assert bridge._external_channel_payload_hash(left) == bridge._external_channel_payload_hash(right)
    assert len(bridge._external_channel_payload_hash(left)) == 64


def test_active_duplicate_reuses_and_updates_existing_row() -> None:
    existing = SimpleNamespace(
        id=11,
        source="legacy",
        session_key="session-1",
        event_type="old",
        recipient=None,
        source_chat_id=None,
        preferred_reply_contact=None,
        payload_hash="placeholder",
        payload_json="{}",
        status="failed",
        replay_count=2,
        last_error="old-error",
        updated_at=None,
    )
    db = _FakeDB(existing)
    event = {
        "type": "message",
        "message": {"sessionKey": "session-1"},
        "route": {"recipient": "+41790000000", "threadId": "thread-9"},
    }

    row = bridge.persist_unresolved_external_channel_event(
        db,
        event=event,
        source="legacy",
        error="retired-ingest",
    )

    assert row is existing
    assert row.event_type == "message"
    assert row.recipient == "+41790000000"
    assert row.source_chat_id == "thread-9"
    assert row.preferred_reply_contact == "+41790000000"
    assert row.last_error == "retired-ingest"
    assert row.status in ACTIVE_STATUSES
    assert db.added == []
    assert db.flush_count == 1


def test_new_event_uses_native_hash_field_and_nested_transaction() -> None:
    db = _FakeDB()
    event = {
        "event_type": "message",
        "session_key": "session-2",
        "recipient": "+41791111111",
    }

    row = bridge.persist_unresolved_external_channel_event(
        db,
        event=event,
        source="legacy",
        error="retired-ingest",
    )

    assert row is db.added[0]
    assert row.payload_hash == bridge._external_channel_payload_hash(event)
    assert row.session_key == "session-2"
    assert row.status == "pending"
    assert row.replay_count == 0
    assert db.nested_count == 1
    assert db.flush_count == 1


def test_service_package_has_no_external_channel_monkey_patch() -> None:
    service_init = (ROOT / "backend" / "app" / "services" / "__init__.py").read_text(encoding="utf-8")
    assert "external_channel_unresolved_store" not in service_init
    assert "apply_external_channel_unresolved_store_patch" not in service_init


def test_obsolete_patch_modules_are_absent() -> None:
    services = ROOT / "backend" / "app" / "services"
    assert not (services / "external_channel_unresolved_store.py").exists()
    assert not (services / "external_channel_payload_hash.py").exists()
