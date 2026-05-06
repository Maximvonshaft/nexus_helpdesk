from __future__ import annotations

from datetime import datetime, timezone

from app.operator_models import OperatorTask
from app.services.operator_queue import serialize_operator_task, transition_operator_task


class FakeQuery:
    def __init__(self, row):
        self.row = row

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.row


class FakeDB:
    def __init__(self, row):
        self.row = row
        self.flushed = False

    def query(self, model):
        return FakeQuery(self.row)

    def flush(self):
        self.flushed = True


def _task() -> OperatorTask:
    return OperatorTask(
        id=1,
        source_type="webchat",
        source_id="wc_demo",
        ticket_id=10,
        webchat_conversation_id=None,
        unresolved_event_id=None,
        task_type="handoff",
        status="pending",
        priority=40,
        reason_code="customer_requested_human",
        payload_json='{"ticket_no": "T-1"}',
        created_at=datetime(2026, 5, 6, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 6, tzinfo=timezone.utc),
    )


def test_serialize_operator_task_payload():
    task = _task()
    payload = serialize_operator_task(task)

    assert payload["id"] == 1
    assert payload["payload_json"]["ticket_no"] == "T-1"


def test_transition_operator_task_assigns_actor():
    task = _task()
    db = FakeDB(task)

    row = transition_operator_task(db, task_id=1, action="assign", actor_id=99)

    assert row.status == "assigned"
    assert row.assignee_id == 99
    assert db.flushed is True


def test_transition_operator_task_resolves():
    task = _task()
    db = FakeDB(task)

    row = transition_operator_task(db, task_id=1, action="resolve", actor_id=99)

    assert row.status == "resolved"
    assert row.resolved_at is not None


def test_transition_operator_task_missing_raises():
    db = FakeDB(None)

    try:
        transition_operator_task(db, task_id=404, action="assign", actor_id=99)
    except ValueError as exc:
        assert str(exc) == "operator_task_not_found"
    else:
        raise AssertionError("expected ValueError")
