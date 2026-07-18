from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from app.enums import MessageStatus
from app.services import message_dispatch
from app.services.outbound_dispatch_transaction_boundary import (
    dispatch_pending_messages,
    reclaim_stale_processing_messages,
)
from app.utils.time import utc_now


class _FakeQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, value):
        self._rows = self._rows[:value]
        return self

    def with_for_update(self, *args, **kwargs):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeDB:
    def __init__(self, rows):
        self.rows = {row.id: row for row in rows}
        self.commits = 0
        self.rollbacks = 0
        self.bind = None

    def query(self, model):
        return _FakeQuery(self.rows.values())

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _message(message_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=message_id,
        ticket_id=1000 + message_id,
        created_by=None,
        channel=SimpleNamespace(value="email"),
        created_at=utc_now(),
        retry_count=0,
        max_retries=3,
        status=MessageStatus.processing,
        provider_status="processing",
        error_message=None,
        failure_code=None,
        failure_reason=None,
        last_attempt_at=None,
        next_retry_at=None,
        locked_at=utc_now() - timedelta(hours=1),
        locked_by="worker-test",
    )


def test_reclaim_stale_processing_returns_attempt_to_retry_state(monkeypatch):
    row = _message(11)
    db = _FakeDB([row])
    events: list[dict] = []

    monkeypatch.setattr(
        message_dispatch,
        "external_channel_values",
        lambda: ["email", "whatsapp"],
    )
    monkeypatch.setattr(
        message_dispatch,
        "log_event",
        lambda *args, **kwargs: events.append(kwargs),
    )

    recovered = reclaim_stale_processing_messages(db)

    assert recovered == 1
    assert db.commits == 1
    assert row.status == MessageStatus.pending
    assert row.retry_count == 1
    assert row.failure_code == "worker_lease_expired"
    assert row.locked_at is None
    assert row.locked_by is None
    assert events[0]["payload"]["failure_code"] == "worker_lease_expired"


def test_reclaim_stale_processing_marks_exhausted_attempt_dead(monkeypatch):
    row = _message(12)
    row.retry_count = 2
    row.max_retries = 3
    db = _FakeDB([row])

    monkeypatch.setattr(message_dispatch, "external_channel_values", lambda: ["email"])
    monkeypatch.setattr(message_dispatch, "log_event", lambda *args, **kwargs: None)

    recovered = reclaim_stale_processing_messages(db)

    assert recovered == 1
    assert row.status == MessageStatus.dead
    assert row.retry_count == 3
    assert row.failure_code == "worker_lease_expired"
    assert row.next_retry_at is None


def test_dispatch_pending_messages_recovers_one_failed_attempt_and_continues(monkeypatch):
    first = _message(1)
    second = _message(2)
    db = _FakeDB([first, second])
    processed_ids: list[int] = []

    monkeypatch.setattr(message_dispatch, "_external_dispatch_block_reason", lambda: None)
    monkeypatch.setattr(
        message_dispatch,
        "claim_pending_messages",
        lambda db, limit=None, worker_id=None: [first, second],
    )
    monkeypatch.setattr(message_dispatch, "log_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "app.services.outbound_dispatch_transaction_boundary.reclaim_stale_processing_messages",
        lambda db, limit=None: 0,
    )

    def fake_process(db_arg, message):
        processed_ids.append(message.id)
        if message.id == 1:
            raise RuntimeError("provider exploded")
        message.status = MessageStatus.sent
        return message

    monkeypatch.setattr(message_dispatch, "process_outbound_message", fake_process)

    processed = dispatch_pending_messages(
        db,
        worker_id="worker-test",
    )

    assert processed_ids == [1, 2]
    assert [row.id for row in processed] == [1, 2]
    assert db.rollbacks == 1
    assert db.commits == 2
    assert first.status == MessageStatus.pending
    assert first.retry_count == 1
    assert first.failure_code == "retryable_dispatch_error"
    assert first.locked_at is None
    assert first.locked_by is None
    assert second.status == MessageStatus.sent


def test_dispatch_pending_messages_marks_dead_when_recovered_attempt_exhausts_retries(monkeypatch):
    row = _message(7)
    row.retry_count = 2
    row.max_retries = 3
    db = _FakeDB([row])

    monkeypatch.setattr(message_dispatch, "_external_dispatch_block_reason", lambda: None)
    monkeypatch.setattr(
        message_dispatch,
        "claim_pending_messages",
        lambda db, limit=None, worker_id=None: [row],
    )
    monkeypatch.setattr(
        message_dispatch,
        "process_outbound_message",
        lambda db, message: (_ for _ in ()).throw(
            RuntimeError("last retry failed")
        ),
    )
    monkeypatch.setattr(message_dispatch, "log_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "app.services.outbound_dispatch_transaction_boundary.reclaim_stale_processing_messages",
        lambda db, limit=None: 0,
    )

    processed = dispatch_pending_messages(
        db,
        worker_id="worker-test",
    )

    assert [item.id for item in processed] == [7]
    assert db.rollbacks == 1
    assert db.commits == 1
    assert row.status == MessageStatus.dead
    assert row.retry_count == 3
    assert row.failure_code == "max_retries"
    assert row.next_retry_at is None
