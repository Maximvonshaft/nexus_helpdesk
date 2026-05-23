from __future__ import annotations

from types import SimpleNamespace

from app.enums import MessageStatus
from app.services import message_dispatch
from app.services.outbound_dispatch_transaction_boundary import apply_outbound_dispatch_transaction_boundary_patch


class _FakeQuery:
    def __init__(self, row):
        self._row = row

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._row


class _FakeDB:
    def __init__(self, rows):
        self.rows = {row.id: row for row in rows}
        self.commits = 0
        self.rollbacks = 0

    def query(self, model):
        return _FakeQuery(next(iter(self.rows.values())))

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _message(message_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=message_id,
        ticket_id=1000 + message_id,
        created_by=None,
        retry_count=0,
        max_retries=3,
        status=MessageStatus.processing,
        provider_status='processing',
        error_message=None,
        failure_code=None,
        failure_reason=None,
        last_attempt_at=None,
        next_retry_at=None,
        locked_at='locked',
        locked_by='worker-test',
    )


def test_dispatch_pending_messages_recovers_one_failed_attempt_and_continues(monkeypatch):
    apply_outbound_dispatch_transaction_boundary_patch()

    first = _message(1)
    second = _message(2)
    db = _FakeDB([first, second])
    processed_ids: list[int] = []

    monkeypatch.setattr(message_dispatch, '_external_dispatch_block_reason', lambda: None)
    monkeypatch.setattr(message_dispatch, 'claim_pending_messages', lambda db, limit=None, worker_id=None: [first, second])
    monkeypatch.setattr(message_dispatch, 'log_event', lambda *args, **kwargs: None)

    def fake_process(db_arg, message):
        processed_ids.append(message.id)
        if message.id == 1:
            raise RuntimeError('provider exploded')
        message.status = MessageStatus.sent
        return message

    monkeypatch.setattr(message_dispatch, 'process_outbound_message', fake_process)

    processed = message_dispatch.dispatch_pending_messages(db, worker_id='worker-test')

    assert processed_ids == [1, 2]
    assert [row.id for row in processed] == [1, 2]
    assert db.rollbacks == 1
    assert db.commits == 2
    assert first.status == MessageStatus.pending
    assert first.retry_count == 1
    assert first.failure_code == 'retryable_dispatch_error'
    assert first.locked_at is None
    assert first.locked_by is None
    assert second.status == MessageStatus.sent


def test_dispatch_pending_messages_marks_dead_when_recovered_attempt_exhausts_retries(monkeypatch):
    apply_outbound_dispatch_transaction_boundary_patch()

    row = _message(7)
    row.retry_count = 2
    row.max_retries = 3
    db = _FakeDB([row])

    monkeypatch.setattr(message_dispatch, '_external_dispatch_block_reason', lambda: None)
    monkeypatch.setattr(message_dispatch, 'claim_pending_messages', lambda db, limit=None, worker_id=None: [row])
    monkeypatch.setattr(message_dispatch, 'process_outbound_message', lambda db, message: (_ for _ in ()).throw(RuntimeError('last retry failed')))
    monkeypatch.setattr(message_dispatch, 'log_event', lambda *args, **kwargs: None)

    processed = message_dispatch.dispatch_pending_messages(db, worker_id='worker-test')

    assert [item.id for item in processed] == [7]
    assert db.rollbacks == 1
    assert db.commits == 1
    assert row.status == MessageStatus.dead
    assert row.retry_count == 3
    assert row.failure_code == 'max_retries'
    assert row.next_retry_at is None
