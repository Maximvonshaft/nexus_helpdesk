from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

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
    engine = create_engine("sqlite:///:memory:", future=True)
    WebchatFastIdempotency.metadata.create_all(engine, tables=[WebchatFastIdempotency.__table__])
    Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _hash(body: str = "hello") -> str:
    return compute_request_hash(
        tenant_key="default",
        channel_key="website",
        session_id="session-1",
        client_message_id="client-1",
        body=body,
        recent_context=[],
    )


def test_same_key_same_hash_processing_not_stale_returns_processing(db_session):
    first = begin_webchat_fast_idempotency(
        db_session,
        tenant_key="default",
        session_id="session-1",
        client_message_id="client-1",
        request_hash=_hash(),
        owner_request_id="req-1",
        lock_seconds=60,
    )
    assert first.kind == "owner"
    second = begin_webchat_fast_idempotency(
        db_session,
        tenant_key="default",
        session_id="session-1",
        client_message_id="client-1",
        request_hash=_hash(),
        owner_request_id="req-2",
        lock_seconds=60,
    )
    assert second.kind == "processing"
    assert second.error_code == "request_processing"


def test_same_key_different_hash_conflict(db_session):
    begin_webchat_fast_idempotency(
        db_session,
        tenant_key="default",
        session_id="session-1",
        client_message_id="client-1",
        request_hash=_hash("a"),
        owner_request_id="req-1",
    )
    second = begin_webchat_fast_idempotency(
        db_session,
        tenant_key="default",
        session_id="session-1",
        client_message_id="client-1",
        request_hash=_hash("b"),
        owner_request_id="req-2",
    )
    assert second.kind == "conflict"
    assert second.error_code == "idempotency_key_reused_with_different_payload"


def test_stale_processing_takeover(db_session):
    first = begin_webchat_fast_idempotency(
        db_session,
        tenant_key="default",
        session_id="session-1",
        client_message_id="client-1",
        request_hash=_hash(),
        owner_request_id="req-1",
    )
    first.row.locked_until = utc_now() - timedelta(seconds=1)
    db_session.flush()
    second = begin_webchat_fast_idempotency(
        db_session,
        tenant_key="default",
        session_id="session-1",
        client_message_id="client-1",
        request_hash=_hash(),
        owner_request_id="req-2",
    )
    assert second.kind == "owner"
    assert second.row.owner_request_id == "req-2"
    assert second.row.attempt_count == 2


def test_done_replay(db_session):
    first = begin_webchat_fast_idempotency(
        db_session,
        tenant_key="default",
        session_id="session-1",
        client_message_id="client-1",
        request_hash=_hash(),
        owner_request_id="req-1",
    )
    mark_webchat_fast_done(db_session, first.row, response_json={"reply": "Hello"})
    second = begin_webchat_fast_idempotency(
        db_session,
        tenant_key="default",
        session_id="session-1",
        client_message_id="client-1",
        request_hash=_hash(),
        owner_request_id="req-2",
    )
    assert second.kind == "replay"
    assert second.response_json == {"reply": "Hello"}


def test_failed_retry_policy(db_session):
    first = begin_webchat_fast_idempotency(
        db_session,
        tenant_key="default",
        session_id="session-1",
        client_message_id="client-1",
        request_hash=_hash(),
        owner_request_id="req-1",
    )
    mark_webchat_fast_failed(db_session, first.row, error_code="ai_unavailable")
    second = begin_webchat_fast_idempotency(
        db_session,
        tenant_key="default",
        session_id="session-1",
        client_message_id="client-1",
        request_hash=_hash(),
        owner_request_id="req-2",
    )
    assert second.kind == "owner"


def test_cleanup_expired_rows(db_session):
    first = begin_webchat_fast_idempotency(
        db_session,
        tenant_key="default",
        session_id="session-1",
        client_message_id="client-1",
        request_hash=_hash(),
        owner_request_id="req-1",
    )
    first.row.expires_at = utc_now() - timedelta(seconds=1)
    db_session.flush()
    assert cleanup_expired_webchat_fast_idempotency(db_session) == 1
    remaining = db_session.execute(select(WebchatFastIdempotency)).scalars().all()
    assert remaining == []
