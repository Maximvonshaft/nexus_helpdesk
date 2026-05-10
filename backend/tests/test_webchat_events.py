from __future__ import annotations

from app.api.webchat_events import _capped_wait_ms, _list_events, _wait_for_events


class FakeColumn:
    def __gt__(self, other):
        return self

    def __eq__(self, other):
        return self

    def asc(self):
        return self


class FakeEventModel:
    id = FakeColumn()
    conversation_id = FakeColumn()
    ticket_id = FakeColumn()


class FakeEvent:
    def __init__(self, event_id: int, event_type: str = "message.created") -> None:
        self.id = event_id
        self.event_type = event_type
        self.payload_json = '{"ok": true}'
        self.created_at = None


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows
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


def test_list_events_caps_limit_and_reports_has_more(monkeypatch):
    monkeypatch.setattr("app.api.webchat_events.WebchatEvent", FakeEventModel)
    rows = [FakeEvent(i) for i in range(1, 200)]
    result = _list_events(FakeDB(rows), after_id=0, limit=500)

    assert len(result["events"]) == 100
    assert result["has_more"] is True
    assert result["events"][0]["event_type"] == "message.created"


def test_wait_for_events_returns_immediately_when_rows_exist(monkeypatch):
    monkeypatch.setattr("app.api.webchat_events.WebchatEvent", FakeEventModel)
    rows = [FakeEvent(10, "ai_turn.queued")]
    result = _wait_for_events(FakeDB(rows), after_id=0, limit=10, wait_ms=25000)

    assert result["events"][0]["id"] == 10
    assert result["events"][0]["event_type"] == "ai_turn.queued"
    assert result["wait_ms"] <= 5000


def test_wait_for_events_empty_timeout_returns_empty(monkeypatch):
    monkeypatch.setattr("app.api.webchat_events.WebchatEvent", FakeEventModel)
    result = _wait_for_events(FakeDB([]), after_id=0, limit=10, wait_ms=1)

    assert result["events"] == []
    assert result["has_more"] is False


def test_wait_ms_is_capped(monkeypatch):
    monkeypatch.setenv("WEBCHAT_EVENTS_MAX_WAIT_MS", "3000")

    assert _capped_wait_ms(25000) == 3000
