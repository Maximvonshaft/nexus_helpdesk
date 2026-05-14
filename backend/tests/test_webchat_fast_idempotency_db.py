from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import Insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql import Select

from app.services.webchat_fast_idempotency_db import (
    WebchatFastIdempotency,
    begin_webchat_fast_idempotency,
    cleanup_expired_webchat_fast_idempotency,
    compute_request_hash,
    mark_webchat_fast_done,
    mark_webchat_fast_failed,
)
from app.utils.time import utc_now

pytestmark = pytest.mark.fast_lane_v2_2_2


@pytest.fixture()
def db_session():
    engine = create_engine('sqlite:///:memory:', future=True)
    WebchatFastIdempotency.metadata.create_all(engine, tables=[WebchatFastIdempotency.__table__])
    Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _hash(body: str = 'hello') -> str:
    return compute_request_hash(
        tenant_key='default',
        channel_key='website',
        session_id='session-1',
        client_message_id='client-1',
        body=body,
        recent_context=[],
    )


def test_same_key_same_hash_processing_not_stale_returns_processing(db_session):
    first = begin_webchat_fast_idempotency(
        db_session, tenant_key='default', session_id='session-1', client_message_id='client-1', request_hash=_hash(), owner_request_id='req-1', lock_seconds=60
    )
    assert first.kind == 'owner'
    second = begin_webchat_fast_idempotency(
        db_session, tenant_key='default', session_id='session-1', client_message_id='client-1', request_hash=_hash(), owner_request_id='req-2', lock_seconds=60
    )
    assert second.kind == 'processing'
    assert second.error_code == 'request_processing'


def test_same_key_different_hash_conflict(db_session):
    begin_webchat_fast_idempotency(db_session, tenant_key='default', session_id='session-1', client_message_id='client-1', request_hash=_hash('a'), owner_request_id='req-1')
    second = begin_webchat_fast_idempotency(db_session, tenant_key='default', session_id='session-1', client_message_id='client-1', request_hash=_hash('b'), owner_request_id='req-2')
    assert second.kind == 'conflict'
    assert second.error_code == 'idempotency_key_reused_with_different_payload'


def test_stale_processing_takeover(db_session):
    first = begin_webchat_fast_idempotency(db_session, tenant_key='default', session_id='session-1', client_message_id='client-1', request_hash=_hash(), owner_request_id='req-1')
    first.row.locked_until = utc_now() - timedelta(seconds=1)
    db_session.flush()
    second = begin_webchat_fast_idempotency(db_session, tenant_key='default', session_id='session-1', client_message_id='client-1', request_hash=_hash(), owner_request_id='req-2')
    assert second.kind == 'owner'
    assert second.row.owner_request_id == 'req-2'
    assert second.row.attempt_count == 2


def test_done_replay(db_session):
    first = begin_webchat_fast_idempotency(db_session, tenant_key='default', session_id='session-1', client_message_id='client-1', request_hash=_hash(), owner_request_id='req-1')
    mark_webchat_fast_done(db_session, first.row, response_json={'reply': 'Hello'})
    second = begin_webchat_fast_idempotency(db_session, tenant_key='default', session_id='session-1', client_message_id='client-1', request_hash=_hash(), owner_request_id='req-2')
    assert second.kind == 'replay'
    assert second.response_json == {'reply': 'Hello'}


def test_failed_retry_policy(db_session):
    first = begin_webchat_fast_idempotency(db_session, tenant_key='default', session_id='session-1', client_message_id='client-1', request_hash=_hash(), owner_request_id='req-1')
    mark_webchat_fast_failed(db_session, first.row, error_code='ai_unavailable')
    retryable = begin_webchat_fast_idempotency(db_session, tenant_key='default', session_id='session-1', client_message_id='client-1', request_hash=_hash(), owner_request_id='req-2')
    assert retryable.kind == 'owner'
    mark_webchat_fast_failed(db_session, retryable.row, error_code='ai_invalid_output')
    parser_retryable = begin_webchat_fast_idempotency(db_session, tenant_key='default', session_id='session-1', client_message_id='client-1', request_hash=_hash(), owner_request_id='req-3')
    assert parser_retryable.kind == 'owner'
    mark_webchat_fast_failed(db_session, parser_retryable.row, error_code='business_rule_violation')
    non_retryable = begin_webchat_fast_idempotency(db_session, tenant_key='default', session_id='session-1', client_message_id='client-1', request_hash=_hash(), owner_request_id='req-4')
    assert non_retryable.kind == 'failed_non_retryable'


def test_cleanup_expired_rows(db_session):
    first = begin_webchat_fast_idempotency(db_session, tenant_key='default', session_id='session-1', client_message_id='client-1', request_hash=_hash(), owner_request_id='req-1')
    first.row.expires_at = utc_now() - timedelta(seconds=1)
    db_session.flush()
    assert cleanup_expired_webchat_fast_idempotency(db_session) == 1
    assert db_session.execute(select(WebchatFastIdempotency)).scalars().all() == []


class _FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value


class _FakePostgresSession:
    def __init__(self, *, insert_id, row, integrity_error=False):
        self.insert_id = insert_id
        self.row = row
        self.integrity_error = integrity_error
        self.flush_calls = 0
        self.statements = []
        self.bind = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))

    def get_bind(self):
        return self.bind

    def execute(self, stmt):
        self.statements.append(stmt)
        if len(self.statements) == 1:
            assert isinstance(stmt, Insert)
            return _FakeResult(self.insert_id)
        assert isinstance(stmt, Select)
        assert stmt._for_update_arg is not None
        return _FakeResult(self.row)

    def flush(self):
        if self.integrity_error:
            raise IntegrityError('stmt', 'params', Exception('duplicate'))
        self.flush_calls += 1


class _Nested:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeCompatibleSession:
    def __init__(self):
        self.bind = SimpleNamespace(dialect=SimpleNamespace(name='sqlite'))
        self.row = None
        self.add_calls = 0
        self.flush_calls = 0
        self.query_count = 0

    def get_bind(self):
        return self.bind

    def begin_nested(self):
        return _Nested()

    def add(self, row):
        self.add_calls += 1
        self.row = row

    def flush(self):
        self.flush_calls += 1
        if self.flush_calls == 1:
            raise IntegrityError('stmt', 'params', Exception('duplicate'))

    def execute(self, stmt):
        self.query_count += 1
        return _FakeResult(self.row)


def test_postgres_sql_level_locking_proves_only_one_owner_for_existing_processing():
    row = WebchatFastIdempotency(
        tenant_key='default', session_id='session-1', client_message_id='client-1', request_hash=_hash(), status='processing',
        locked_until=utc_now() + timedelta(seconds=60), owner_request_id='req-1', attempt_count=1, expires_at=utc_now() + timedelta(seconds=600),
    )
    db_session = _FakePostgresSession(insert_id=None, row=row)
    result = begin_webchat_fast_idempotency(db_session, tenant_key='default', session_id='session-1', client_message_id='client-1', request_hash=_hash(), owner_request_id='req-2')
    assert result.kind == 'processing'
    assert result.error_code == 'request_processing'
    assert db_session.flush_calls == 0


def test_postgres_takeover_increments_attempt_count_once():
    row = WebchatFastIdempotency(
        tenant_key='default', session_id='session-1', client_message_id='client-1', request_hash=_hash(), status='processing',
        locked_until=utc_now() - timedelta(seconds=1), owner_request_id='req-1', attempt_count=1, expires_at=utc_now() + timedelta(seconds=600),
    )
    db_session = _FakePostgresSession(insert_id=None, row=row)
    result = begin_webchat_fast_idempotency(db_session, tenant_key='default', session_id='session-1', client_message_id='client-1', request_hash=_hash(), owner_request_id='req-2')
    assert result.kind == 'owner'
    assert result.row.owner_request_id == 'req-2'
    assert result.row.attempt_count == 2
    assert db_session.flush_calls == 1


def test_compatible_unique_conflict_does_not_break_outer_transaction_semantics():
    row = WebchatFastIdempotency(
        tenant_key='default', session_id='session-1', client_message_id='client-1', request_hash=_hash(), status='processing',
        locked_until=utc_now() + timedelta(seconds=60), owner_request_id='req-1', attempt_count=1, expires_at=utc_now() + timedelta(seconds=600),
    )
    db_session = _FakeCompatibleSession()
    db_session.row = row
    result = begin_webchat_fast_idempotency(db_session, tenant_key='default', session_id='session-1', client_message_id='client-1', request_hash=_hash(), owner_request_id='req-2')
    assert result.kind == 'processing'
    assert result.error_code == 'request_processing'
    assert db_session.add_calls == 0
    assert db_session.query_count >= 1
