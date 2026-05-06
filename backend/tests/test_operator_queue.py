from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.operator_models import OperatorTask
from app.services import operator_queue


class FakeQuery:
    def __init__(self, row):
        self.row = row

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def first(self):
        return self.row

    def all(self):
        return [] if self.row is None else [self.row]


class FakeDB:
    def __init__(self, row=None):
        self.row = row
        self.flushed = False
        self.added = []

    def query(self, model):
        return FakeQuery(self.row)

    def add(self, row):
        self.added.append(row)
        if getattr(row, "id", None) is None:
            row.id = 100 + len(self.added)

    def flush(self):
        self.flushed = True


def _task(**overrides) -> OperatorTask:
    values = dict(
        id=1,
        source_type="webchat",
        source_id="wc_demo",
        ticket_id=10,
        webchat_conversation_id=None,
        unresolved_event_id=None,
        task_type="handoff",
        status="pending",
        priority=40,
        assignee_id=None,
        reason_code="customer_requested_human",
        payload_json='{"ticket_no": "T-1"}',
        created_at=datetime(2026, 5, 6, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 6, tzinfo=timezone.utc),
        resolved_at=None,
    )
    values.update(overrides)
    return OperatorTask(**values)


def test_serialize_operator_task_payload():
    payload = operator_queue.serialize_operator_task(_task())

    assert payload["id"] == 1
    assert payload["payload_json"]["ticket_no"] == "T-1"


def test_transition_operator_task_assigns_actor():
    task = _task()
    db = FakeDB(task)

    row = operator_queue.transition_operator_task(db, task_id=1, action="assign", actor_id=99)

    assert row.status == "assigned"
    assert row.assignee_id == 99
    assert db.flushed is True


def test_transition_operator_task_resolves():
    task = _task()
    db = FakeDB(task)

    row = operator_queue.transition_operator_task(db, task_id=1, action="resolve", actor_id=99)

    assert row.status == "resolved"
    assert row.resolved_at is not None


def test_transition_operator_task_drop_marks_terminal_with_resolved_at():
    task = _task()
    db = FakeDB(task)

    row = operator_queue.transition_operator_task(db, task_id=1, action="drop", actor_id=99)

    assert row.status == "dropped"
    assert row.resolved_at is not None
    assert row.updated_at is not None


def test_transition_operator_task_replay_marks_terminal_contract():
    task = _task(unresolved_event_id=123, source_type="openclaw", source_id="123", task_type="bridge_unresolved")
    db = FakeDB(task)

    row = operator_queue.transition_operator_task(db, task_id=1, action="replay", actor_id=99)

    assert row.status == "replayed"
    assert row.resolved_at is not None


def test_transition_operator_task_unsupported_action_raises_value_error_without_mutating():
    task = _task(status="pending", assignee_id=None)
    db = FakeDB(task)

    with pytest.raises(ValueError, match="unsupported_operator_task_action"):
        operator_queue.transition_operator_task(db, task_id=1, action="escalate", actor_id=99)

    assert task.status == "pending"
    assert task.assignee_id is None


def test_transition_operator_task_missing_raises():
    db = FakeDB(None)

    with pytest.raises(ValueError, match="operator_task_not_found"):
        operator_queue.transition_operator_task(db, task_id=404, action="assign", actor_id=99)


def test_transition_note_is_preserved_in_webchat_event(monkeypatch):
    task = _task(webchat_conversation_id=88, ticket_id=10)
    db = FakeDB(task)
    events = []

    def fake_write_webchat_event(db_arg, *, conversation_id, ticket_id, event_type, payload):
        events.append(
            {
                "conversation_id": conversation_id,
                "ticket_id": ticket_id,
                "event_type": event_type,
                "payload": payload,
            }
        )

    monkeypatch.setattr(operator_queue, "write_webchat_event", fake_write_webchat_event)

    row = operator_queue.transition_operator_task(db, task_id=1, action="resolve", actor_id=99, note="customer confirmed")

    assert row.status == "resolved"
    assert events
    assert events[-1]["payload"]["note"] == "customer confirmed"


def test_create_operator_task_records_webchat_event(monkeypatch):
    db = FakeDB(None)
    events = []

    def fake_write_webchat_event(db_arg, *, conversation_id, ticket_id, event_type, payload):
        events.append((conversation_id, ticket_id, event_type, payload))

    monkeypatch.setattr(operator_queue, "write_webchat_event", fake_write_webchat_event)

    row = operator_queue.create_operator_task(
        db,
        source_type="webchat",
        source_id="wc_note",
        ticket_id=10,
        webchat_conversation_id=88,
        task_type="handoff",
        reason_code="human_review_required",
        payload={"ticket_no": "T-NOTE"},
    )

    assert row in db.added
    assert row.status == "pending"
    assert events
    assert events[-1][2] == "handoff.requested"
    assert events[-1][3]["operator_task_id"] == row.id
