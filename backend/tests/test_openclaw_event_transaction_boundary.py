from __future__ import annotations

from types import SimpleNamespace

from app.services import openclaw_bridge
from app.services.openclaw_event_transaction_boundary import apply_openclaw_event_transaction_boundary_patch


class _FakeCursorColumn:
    def __eq__(self, other):
        return True


class _FakeCursorModel:
    source = _FakeCursorColumn()


class _FakeQuery:
    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return None


class _FakeDB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0

    def query(self, *args, **kwargs):
        return _FakeQuery()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def flush(self):
        self.flushes += 1


def test_openclaw_event_boundary_recovers_failed_event_and_continues(monkeypatch):
    apply_openclaw_event_transaction_boundary_patch()

    db = _FakeDB()
    events = [
        {"type": "message", "sessionKey": "session-1", "route": {"recipient": "+15550001"}, "cursor": 1},
        {"type": "message", "sessionKey": "session-2", "route": {"recipient": "+15550002"}, "cursor": 2},
    ]
    processed_events: list[str] = []
    unresolved_rows: list[SimpleNamespace] = []
    cursor_values: list[str] = []

    monkeypatch.setattr(openclaw_bridge.settings, "openclaw_bridge_enabled", True)
    monkeypatch.setattr(openclaw_bridge.settings, "openclaw_sync_poll_timeout_seconds", 1)
    monkeypatch.setattr(openclaw_bridge, "OpenClawSyncCursor", _FakeCursorModel)
    monkeypatch.setattr(openclaw_bridge, "wait_openclaw_bridge_events", lambda **kwargs: {"event": events[0]})
    monkeypatch.setattr(openclaw_bridge, "poll_openclaw_bridge_events", lambda **kwargs: {"events": events, "nextCursor": 2})

    def fake_process(db_arg, *, event, source, client=None):
        processed_events.append(event["sessionKey"])
        if event["sessionKey"] == "session-1":
            raise RuntimeError("bad event")
        return True

    def fake_persist_unresolved(db_arg, **kwargs):
        row = SimpleNamespace(status="pending", last_error=None, updated_at=None, **kwargs)
        unresolved_rows.append(row)
        return row

    monkeypatch.setattr(openclaw_bridge, "process_openclaw_inbound_event", fake_process)
    monkeypatch.setattr(openclaw_bridge, "persist_unresolved_openclaw_event", fake_persist_unresolved)
    monkeypatch.setattr(openclaw_bridge, "upsert_openclaw_sync_cursor", lambda db, source, cursor_value: cursor_values.append(cursor_value))

    assert openclaw_bridge.consume_openclaw_events_once(db, timeout_seconds=1) == 1
    assert processed_events == ["session-1", "session-2"]
    assert db.rollbacks == 1
    assert db.commits == 2
    assert cursor_values == ["1", "2"]
    assert len(unresolved_rows) == 1
    assert unresolved_rows[0].session_key == "session-1"
    assert unresolved_rows[0].event_type == "message"
    assert unresolved_rows[0].recipient == "+15550001"
    assert unresolved_rows[0].status == "failed"
    assert unresolved_rows[0].last_error == "Unhandled OpenClaw event exception: RuntimeError"


def test_openclaw_event_boundary_keeps_mcp_client_open_during_event_processing(monkeypatch):
    apply_openclaw_event_transaction_boundary_patch()

    db = _FakeDB()
    seen_clients = []

    class FakeClient:
        def __init__(self):
            self.closed = False

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            self.closed = True

        def events_wait(self, cursor, timeout_seconds):
            return {"events": [{"type": "message", "sessionKey": "session-mcp", "route": {"recipient": "+15550003"}, "cursor": 7}], "nextCursor": 7}

        def events_poll(self, cursor):
            raise AssertionError("events_poll should not be needed")

    fake_client = FakeClient()

    def fake_process(db_arg, *, event, source, client=None):
        assert client is fake_client
        assert client.closed is False
        seen_clients.append(client)
        return True

    monkeypatch.setattr(openclaw_bridge.settings, "openclaw_bridge_enabled", False)
    monkeypatch.setattr(openclaw_bridge.settings, "openclaw_sync_poll_timeout_seconds", 1)
    monkeypatch.setattr(openclaw_bridge, "OpenClawSyncCursor", _FakeCursorModel)
    monkeypatch.setattr(openclaw_bridge, "OpenClawMCPClient", lambda: fake_client)
    monkeypatch.setattr(openclaw_bridge, "_local_mcp_fallback_allowed", lambda: True)
    monkeypatch.setattr(openclaw_bridge, "process_openclaw_inbound_event", fake_process)
    monkeypatch.setattr(openclaw_bridge, "upsert_openclaw_sync_cursor", lambda db, source, cursor_value: None)

    assert openclaw_bridge.consume_openclaw_events_once(db, timeout_seconds=1) == 1
    assert seen_clients == [fake_client]
    assert fake_client.closed is True
    assert db.commits == 1
    assert db.rollbacks == 0
