from __future__ import annotations

import os

os.environ.setdefault('APP_ENV', 'development')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/webchat_stream_replay_semantics.db')
os.environ.setdefault('WEBCHAT_FAST_AI_ENABLED', 'false')

from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.api import webchat_fast
from app.db import Base, SessionLocal, engine
from app.main import app
from app.services import webchat_fast_stream_service
from app.services.webchat_fast_idempotency_db import WebchatFastIdempotency, compute_request_hash
from app.services.webchat_openclaw_stream_adapter import Completed, ContentDelta
from app.utils.time import utc_now

pytestmark = pytest.mark.fast_lane_v2_2_2

client = TestClient(app)


def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        db.execute(delete(WebchatFastIdempotency))
        db.commit()
    finally:
        db.close()


def _settings(enabled: bool = True):
    return SimpleNamespace(
        stream_enabled=enabled,
        stream_require_accept=True,
        openclaw_responses_agent_id='webchat-fast',
    )


def _payload(client_message_id: str = 'client-replay-1') -> dict:
    return {
        'tenant_key': 'default',
        'channel_key': 'website',
        'session_id': 'session-replay-1',
        'client_message_id': client_message_id,
        'body': 'Hi',
        'recent_context': [],
    }


def test_active_processing_row_returns_202_and_does_not_call_openclaw(monkeypatch):
    payload = _payload('client-active')
    request_hash = compute_request_hash(
        tenant_key=payload['tenant_key'],
        channel_key=payload['channel_key'],
        session_id=payload['session_id'],
        client_message_id=payload['client_message_id'],
        body=payload['body'],
        recent_context=payload['recent_context'],
    )
    db = SessionLocal()
    try:
        row = WebchatFastIdempotency(
            tenant_key=payload['tenant_key'],
            session_id=payload['session_id'],
            client_message_id=payload['client_message_id'],
            request_hash=request_hash,
            status='processing',
            locked_until=utc_now() + timedelta(seconds=60),
            owner_request_id='existing-owner',
            attempt_count=1,
            expires_at=utc_now() + timedelta(minutes=10),
        )
        db.add(row)
        db.commit()
    finally:
        db.close()

    calls = {'count': 0}

    async def fake_call_stream(**kwargs):
        calls['count'] += 1
        yield ContentDelta('should not happen')

    monkeypatch.setattr(webchat_fast, 'get_webchat_fast_settings', lambda: _settings(True))
    monkeypatch.setattr(webchat_fast, 'enforce_webchat_fast_rate_limit', lambda *a, **k: None)
    monkeypatch.setattr(webchat_fast_stream_service.openclaw_client, 'call_openclaw_responses_stream', fake_call_stream)

    response = client.post('/api/webchat/fast-reply/stream', json=payload, headers={'Accept': 'text/event-stream'})

    assert response.status_code == 202
    assert response.json()['error_code'] == 'request_processing'
    assert calls['count'] == 0


def test_done_replay_emits_replay_event_and_final_replayed_true(monkeypatch):
    calls = {'count': 0}

    async def fake_call_stream(**kwargs):
        calls['count'] += 1
        yield ContentDelta('{"reply":"Hello","intent":"greeting","tracking_number":null,"handoff_required":false,"handoff_reason":null,"recommended_agent_action":null}')
        yield Completed(full_text='{"reply":"Hello","intent":"greeting","tracking_number":null,"handoff_required":false,"handoff_reason":null,"recommended_agent_action":null}')

    monkeypatch.setattr(webchat_fast, 'get_webchat_fast_settings', lambda: _settings(True))
    monkeypatch.setattr(webchat_fast, 'enforce_webchat_fast_rate_limit', lambda *a, **k: None)
    monkeypatch.setattr(webchat_fast_stream_service.openclaw_client, 'call_openclaw_responses_stream', fake_call_stream)

    first = client.post('/api/webchat/fast-reply/stream', json=_payload('client-replay-done'), headers={'Accept': 'text/event-stream'})
    second = client.post('/api/webchat/fast-reply/stream', json=_payload('client-replay-done'), headers={'Accept': 'text/event-stream'})

    assert first.status_code == 200
    assert second.status_code == 200
    assert 'event: replay' in second.text
    assert '"reply":"Hello"' in second.text
    assert '"replayed":true' in second.text
    assert calls['count'] == 1
