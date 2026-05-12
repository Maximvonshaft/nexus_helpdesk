from __future__ import annotations

import os

os.environ.setdefault('APP_ENV', 'development')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/webchat_stream_feature_flag.db')
os.environ.setdefault('WEBCHAT_FAST_AI_ENABLED', 'false')

import pytest
from fastapi.testclient import TestClient

from app.api import webchat_fast
from app.main import app
from app.services.webchat_fast_ai_service import WebchatFastReplyResult
from app.services.webchat_fast_config import get_webchat_fast_settings
from app.services.webchat_fast_stream_service import StreamBeginOutcome

pytestmark = pytest.mark.fast_lane_v2_2_2

client = TestClient(app)


def _payload(client_message_id: str = 'stream-flag-1') -> dict:
    return {
        'tenant_key': 'default',
        'channel_key': 'website',
        'session_id': 'session-stream-flag',
        'client_message_id': client_message_id,
        'body': 'Hi',
        'recent_context': [],
    }


def test_stream_disabled_env_blocks_stream_but_non_stream_still_works(monkeypatch):
    monkeypatch.setenv('WEBCHAT_FAST_STREAM_ENABLED', 'false')
    get_webchat_fast_settings.cache_clear()
    monkeypatch.setattr(webchat_fast, 'enforce_webchat_fast_rate_limit', lambda *a, **k: None)

    async def fake_generate(**kwargs):
        return WebchatFastReplyResult(
            ok=True, ai_generated=True, reply_source='openclaw_responses', reply='Hello', intent='greeting',
            tracking_number=None, handoff_required=False, handoff_reason=None, recommended_agent_action=None,
            ticket_creation_queued=False, elapsed_ms=10,
        )

    monkeypatch.setattr(webchat_fast, 'generate_webchat_fast_reply', fake_generate)

    stream = client.post('/api/webchat/fast-reply/stream', json=_payload('stream-off'), headers={'Accept': 'text/event-stream'})
    nonstream = client.post('/api/webchat/fast-reply', json=_payload('stream-off-nonstream'))

    assert stream.status_code == 503
    assert stream.json()['error_code'] == 'stream_disabled'
    assert nonstream.status_code == 200
    assert nonstream.json()['reply'] == 'Hello'


def test_stream_enabled_env_allows_stream_path(monkeypatch):
    monkeypatch.setenv('WEBCHAT_FAST_STREAM_ENABLED', 'true')
    get_webchat_fast_settings.cache_clear()
    monkeypatch.setattr(webchat_fast, 'enforce_webchat_fast_rate_limit', lambda *a, **k: None)
    monkeypatch.setattr(webchat_fast, 'prepare_webchat_fast_stream', lambda **kwargs: StreamBeginOutcome(status='owner', request_hash='h', row_id=1))

    async def fake_stream(**kwargs):
        yield 'event: meta\ndata: {"replayed":false}\n\n'
        yield 'event: final\ndata: {"intent":"greeting","handoff_required":false,"ticket_creation_queued":false}\n\n'

    monkeypatch.setattr(webchat_fast, 'stream_webchat_fast_reply_events', fake_stream)

    response = client.post('/api/webchat/fast-reply/stream', json=_payload('stream-on'), headers={'Accept': 'text/event-stream'})

    assert response.status_code == 200
    assert 'text/event-stream' in response.headers['content-type']
    assert 'event: final' in response.text
