from __future__ import annotations

from app.api.webchat_events import _list_events, _wait_for_events


class FakeEvent:
    def __init__(self, event_id: int, event_type: str = "message.created") -> None:
        self.id = event_id
        self.event_type = event_type
        self.payload_json = '{"ok": true}'
        self.created_at = None


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows
        self.after_id = 0
        self.limit_value = 50

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def all(self):
        return self.rows[: self.limit_value]


class FakeDB:
    def __init__(self, rows):
        self.rows = rows

    def query(self, model):
        return FakeQuery(self.rows)


def test_list_events_caps_limit():
    rows = [FakeEvent(i) for i in range(1, 200)]
    events = _list_events(FakeDB(rows), after_id=0, limit=500)

    assert len(events) == 100
    assert events[0]["event_type"] == "message.created"


def test_wait_for_events_returns_immediately_when_rows_exist():
    rows = [FakeEvent(10, "ai_turn.queued")]
    events = _wait_for_events(FakeDB(rows), after_id=0, limit=10, wait_ms=25000)

    assert events[0]["id"] == 10
    assert events[0]["event_type"] == "ai_turn.queued"


def test_wait_for_events_empty_timeout_returns_empty():
    events = _wait_for_events(FakeDB([]), after_id=0, limit=10, wait_ms=1)

    assert events == []
