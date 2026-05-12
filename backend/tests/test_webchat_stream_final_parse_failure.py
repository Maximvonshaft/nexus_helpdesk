from __future__ import annotations

import os

os.environ.setdefault('APP_ENV', 'development')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/webchat_stream_final_parse_failure.db')
os.environ.setdefault('WEBCHAT_FAST_AI_ENABLED', 'false')

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.api import webchat_fast
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import BackgroundJob, Ticket
from app.services import webchat_fast_stream_service
from app.services.webchat_fast_idempotency_db import WebchatFastIdempotency
from app.services.webchat_openclaw_stream_adapter import Completed, ContentDelta

pytestmark = pytest.mark.fast_lane_v2_2_2

client = TestClient(app)


def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        db.execute(delete(BackgroundJob))
        db.execute(delete(Ticket))
        db.execute(delete(WebchatFastIdempotency))
        db.commit()
    finally:
        db.close()


def _settings():
    return SimpleNamespace(stream_enabled=True, stream_require_accept=True, openclaw_responses_agent_id='webchat-fast')


def _payload(client_message_id: str = 'client-invalid-final') -> dict:
    return {
        'tenant_key': 'default',
        'channel_key': 'website',
        'session_id': 'session-invalid-final',
        'client_message_id': client_message_id,
        'body': 'Hi',
        'recent_context': [],
    }


def test_partial_reply_then_invalid_final_rejected_and_failed(monkeypatch):
    async def fake_call_stream(**kwargs):
        yield ContentDelta('{"reply":"Hello there, I can help with that.","intent":"greeting","tracking_number":null,"handoff_required":')
        yield Completed(full_text='{"reply":"Hello there, I can help with that.","intent":"greeting","tracking_number":null,"handoff_required":"bad","handoff_reason":null,"recommended_agent_action":null}')

    monkeypatch.setattr(webchat_fast, 'get_webchat_fast_settings', _settings)
    monkeypatch.setattr(webchat_fast, 'enforce_webchat_fast_rate_limit', lambda *a, **k: None)
    monkeypatch.setattr(webchat_fast_stream_service.openclaw_client, 'call_openclaw_responses_stream', fake_call_stream)

    response = client.post('/api/webchat/fast-reply/stream', json=_payload(), headers={'Accept': 'text/event-stream'})

    assert response.status_code == 200
    assert 'event: error' in response.text
    assert 'ai_invalid_output' in response.text
    assert 'event: final' not in response.text

    db = SessionLocal()
    try:
        row = db.execute(select(WebchatFastIdempotency).where(WebchatFastIdempotency.client_message_id == 'client-invalid-final')).scalar_one()
        assert row.status == 'failed'
        assert row.error_code == 'ai_invalid_output'
        from sqlalchemy import text as sql_text
        assert db.execute(sql_text('select count(*) from tickets')).scalar_one() == 0
        assert db.execute(sql_text('select count(*) from background_jobs')).scalar_one() == 0
    finally:
        db.close()
