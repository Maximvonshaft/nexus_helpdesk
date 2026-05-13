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
from app.services.webchat_fast_idempotency_db import IdempotencyBeginResult, WebchatFastIdempotency

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



@pytest.fixture(autouse=True)
def setup_db():
    from app.db import engine, Base
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)

class FakeRow:
    id = 1


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
    monkeypatch.setenv('OPENCLAW_RESPONSES_STREAM_URL', 'http://127.0.0.1/stream')
    monkeypatch.setenv('OPENCLAW_RESPONSES_STREAM_TOKEN', 'test')
    import app.services.webchat_fast_config
    monkeypatch.setattr(app.services.webchat_fast_config.WebchatFastSettings, 'stream_token', 'test')
    monkeypatch.setattr(app.services.webchat_fast_config.WebchatFastSettings, 'is_openclaw_stream_configured', True)

    monkeypatch.setenv('WEBCHAT_FAST_STREAM_ROLLOUT_PERCENT', '100')
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


def test_stream_rollout_gate_blocks_zero_percent(monkeypatch):
    monkeypatch.setenv('WEBCHAT_FAST_STREAM_ENABLED', 'true')
    monkeypatch.setenv('OPENCLAW_RESPONSES_STREAM_URL', 'http://127.0.0.1/stream')
    monkeypatch.setenv('OPENCLAW_RESPONSES_STREAM_TOKEN', 'test')
    import app.services.webchat_fast_config
    monkeypatch.setattr(app.services.webchat_fast_config.WebchatFastSettings, 'stream_token', 'test')
    monkeypatch.setattr(app.services.webchat_fast_config.WebchatFastSettings, 'is_openclaw_stream_configured', True)

    monkeypatch.setenv('WEBCHAT_FAST_STREAM_ROLLOUT_PERCENT', '0')
    get_webchat_fast_settings.cache_clear()
    
    response = client.post('/api/webchat/fast-reply/stream', json=_payload('stream-off'), headers={'Accept': 'text/event-stream'})
    assert response.status_code == 503
    assert response.json()['error_code'] == 'stream_not_in_rollout'

def test_stream_canary_override_allows_bypass(monkeypatch):
    monkeypatch.setenv('WEBCHAT_FAST_STREAM_ENABLED', 'true')
    monkeypatch.setenv('OPENCLAW_RESPONSES_STREAM_URL', 'http://127.0.0.1/stream')
    monkeypatch.setenv('OPENCLAW_RESPONSES_STREAM_TOKEN', 'test')
    import app.services.webchat_fast_config
    monkeypatch.setattr(app.services.webchat_fast_config.WebchatFastSettings, 'stream_token', 'test')
    monkeypatch.setattr(app.services.webchat_fast_config.WebchatFastSettings, 'is_openclaw_stream_configured', True)

    monkeypatch.setenv('WEBCHAT_FAST_STREAM_ROLLOUT_PERCENT', '0')
    monkeypatch.setenv('APP_ENV', 'development')
    get_webchat_fast_settings.cache_clear()
    
    monkeypatch.setattr(webchat_fast, 'enforce_webchat_fast_rate_limit', lambda *a, **k: None)
    monkeypatch.setattr(webchat_fast, 'prepare_webchat_fast_stream', lambda **kwargs: StreamBeginOutcome(status='owner', request_hash='h', row_id=1))

    async def fake_stream(**kwargs):
        yield 'event: final\ndata: {"intent":"greeting","handoff_required":false,"ticket_creation_queued":false}\n\n'

    monkeypatch.setattr(webchat_fast, 'stream_webchat_fast_reply_events', fake_stream)

    response = client.post('/api/webchat/fast-reply/stream', json=_payload('stream-override'), headers={'Accept': 'text/event-stream', 'X-Nexus-Stream-Canary': '1'})
    assert response.status_code == 200
    assert 'text/event-stream' in response.headers['content-type']

def test_stream_canary_override_fails_for_public_ip_in_production(monkeypatch):
    monkeypatch.setenv('WEBCHAT_FAST_STREAM_ENABLED', 'true')
    monkeypatch.setenv('OPENCLAW_RESPONSES_STREAM_URL', 'http://127.0.0.1/stream')
    monkeypatch.setenv('OPENCLAW_RESPONSES_STREAM_TOKEN', 'test')
    import app.services.webchat_fast_config
    monkeypatch.setattr(app.services.webchat_fast_config.WebchatFastSettings, 'stream_token', 'test')
    monkeypatch.setattr(app.services.webchat_fast_config.WebchatFastSettings, 'is_openclaw_stream_configured', True)

    monkeypatch.setenv('WEBCHAT_FAST_STREAM_ROLLOUT_PERCENT', '0')
    monkeypatch.setenv('APP_ENV', 'production')
    get_webchat_fast_settings.cache_clear()
    
    # We patch the request client host to simulate a non-loopback IP
    original_receive = client.app
    
    class FakeHostMiddleware:
        def __init__(self, app):
            self.app = app
            
        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                scope["client"] = ("172.18.0.10", 12345)
            await self.app(scope, receive, send)
            
    client_prod = TestClient(FakeHostMiddleware(app))

    response = client_prod.post('/api/webchat/fast-reply/stream', json=_payload('stream-override-prod'), headers={'Accept': 'text/event-stream', 'X-Nexus-Stream-Canary': '1'})
    assert response.status_code == 503
    assert response.json()['error_code'] == 'stream_not_in_rollout'

def test_stream_canary_override_allows_bypass_for_loopback_in_production(monkeypatch):
    monkeypatch.setenv('WEBCHAT_FAST_STREAM_ENABLED', 'true')
    monkeypatch.setenv('OPENCLAW_RESPONSES_STREAM_URL', 'http://127.0.0.1/stream')
    monkeypatch.setenv('OPENCLAW_RESPONSES_STREAM_TOKEN', 'test')
    import app.services.webchat_fast_config
    monkeypatch.setattr(app.services.webchat_fast_config.WebchatFastSettings, 'stream_token', 'test')
    monkeypatch.setattr(app.services.webchat_fast_config.WebchatFastSettings, 'is_openclaw_stream_configured', True)

    monkeypatch.setenv('WEBCHAT_FAST_STREAM_ROLLOUT_PERCENT', '0')
    monkeypatch.setenv('APP_ENV', 'production')
    get_webchat_fast_settings.cache_clear()
    
    monkeypatch.setattr(webchat_fast, 'enforce_webchat_fast_rate_limit', lambda *a, **k: None)
    monkeypatch.setattr(webchat_fast, 'prepare_webchat_fast_stream', lambda **kwargs: StreamBeginOutcome(status='owner', request_hash='h', row_id=1))

    async def fake_stream(**kwargs):
        yield 'event: final\ndata: {"intent":"greeting","handoff_required":false,"ticket_creation_queued":false}\n\n'

    monkeypatch.setattr(webchat_fast, 'stream_webchat_fast_reply_events', fake_stream)
    
    class FakeHostMiddlewareLoopback:
        def __init__(self, app):
            self.app = app
            
        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                scope["client"] = ("127.0.0.1", 12345)
            await self.app(scope, receive, send)
            
    client_loopback = TestClient(FakeHostMiddlewareLoopback(app))

    response = client_loopback.post('/api/webchat/fast-reply/stream', json=_payload('stream-override-loopback'), headers={'Accept': 'text/event-stream', 'X-Nexus-Stream-Canary': '1'})
    assert response.status_code == 200
    assert 'text/event-stream' in response.headers['content-type']

def test_stream_deterministic_rollout_hashing():
    from app.services.webchat_fast_rollout import is_stream_rollout_selected
    
    selected_count = 0
    total = 1000
    for i in range(total):
        if is_stream_rollout_selected(tenant_key="t1", channel_key="c1", session_id=f"session-{i}", rollout_percent=20):
            selected_count += 1
            
    assert 150 < selected_count < 250
    
    for _ in range(10):
        assert is_stream_rollout_selected(tenant_key="t1", channel_key="c1", session_id="fixed-session-123", rollout_percent=50) == is_stream_rollout_selected(tenant_key="t1", channel_key="c1", session_id="fixed-session-123", rollout_percent=50)

import os
import pytest
from unittest.mock import patch
from app.services.webchat_fast_config import get_webchat_fast_settings

def test_config_rules():
    # Production stream disabled
    with patch.dict(os.environ, {
        "APP_ENV": "production",
            "WEBCHAT_FAST_AI_ENABLED": "true",
        "WEBCHAT_FAST_STREAM_ENABLED": "false",
        "OPENCLAW_RESPONSES_URL": "http://100.64.0.1/responses",
        "OPENCLAW_RESPONSES_TOKEN_FILE": "/dev/null",
        "OPENCLAW_RESPONSES_STREAM_URL": "",
        "OPENCLAW_RESPONSES_STREAM_TOKEN_FILE": "",
    }):
        get_webchat_fast_settings.cache_clear()
        settings = get_webchat_fast_settings()
        assert settings.enabled is True
        assert settings.stream_enabled is False

    # Production stream enabled missing URL
    with patch.dict(os.environ, {
        "APP_ENV": "production",
            "WEBCHAT_FAST_AI_ENABLED": "true",
        "WEBCHAT_FAST_STREAM_ENABLED": "true",
        "OPENCLAW_RESPONSES_URL": "http://100.64.0.1/responses",
        "OPENCLAW_RESPONSES_TOKEN_FILE": "/dev/null",
        "OPENCLAW_RESPONSES_STREAM_URL": "",
        "OPENCLAW_RESPONSES_STREAM_TOKEN_FILE": "/dev/null",
    }):
        get_webchat_fast_settings.cache_clear()
        with pytest.raises(RuntimeError, match="OPENCLAW_RESPONSES_STREAM_URL is required in production when stream is enabled"):
            get_webchat_fast_settings()

    # Production stream enabled missing Token File
    with patch.dict(os.environ, {
        "APP_ENV": "production",
            "WEBCHAT_FAST_AI_ENABLED": "true",
        "WEBCHAT_FAST_STREAM_ENABLED": "true",
        "OPENCLAW_RESPONSES_URL": "http://100.64.0.1/responses",
        "OPENCLAW_RESPONSES_TOKEN_FILE": "/dev/null",
        "OPENCLAW_RESPONSES_STREAM_URL": "http://100.64.0.2/responses",
        "OPENCLAW_RESPONSES_STREAM_TOKEN_FILE": "",
    }):
        get_webchat_fast_settings.cache_clear()
        with pytest.raises(RuntimeError, match="OPENCLAW_RESPONSES_STREAM_TOKEN_FILE is required in production when stream is enabled"):
            get_webchat_fast_settings()

    # Production stream enabled token plain text
    with patch.dict(os.environ, {
        "APP_ENV": "production",
            "WEBCHAT_FAST_AI_ENABLED": "true",
        "WEBCHAT_FAST_STREAM_ENABLED": "true",
        "OPENCLAW_RESPONSES_URL": "http://100.64.0.1/responses",
        "OPENCLAW_RESPONSES_TOKEN_FILE": "/dev/null",
        "OPENCLAW_RESPONSES_STREAM_URL": "http://100.64.0.2/responses",
        "OPENCLAW_RESPONSES_STREAM_TOKEN_FILE": "/dev/null",
        "OPENCLAW_RESPONSES_STREAM_TOKEN": "secret",
    }):
        get_webchat_fast_settings.cache_clear()
        with pytest.raises(RuntimeError, match="OPENCLAW_RESPONSES_STREAM_TOKEN is forbidden in production"):
            get_webchat_fast_settings()

    # Stream URL private/tailnet pass
    with patch.dict(os.environ, {
        "APP_ENV": "production",
            "WEBCHAT_FAST_AI_ENABLED": "true",
        "WEBCHAT_FAST_STREAM_ENABLED": "true",
        "OPENCLAW_RESPONSES_URL": "http://100.64.0.1/responses",
        "OPENCLAW_RESPONSES_TOKEN_FILE": "/dev/null",
        "OPENCLAW_RESPONSES_STREAM_URL": "http://100.64.0.2/responses",
        "OPENCLAW_RESPONSES_STREAM_TOKEN_FILE": "/dev/null",
    }):
        get_webchat_fast_settings.cache_clear()
        settings = get_webchat_fast_settings()
        assert settings.openclaw_responses_stream_url == "http://100.64.0.2/responses"

    # Stream URL public host validation failure
    with patch.dict(os.environ, {
        "APP_ENV": "production",
            "WEBCHAT_FAST_AI_ENABLED": "true",
        "WEBCHAT_FAST_STREAM_ENABLED": "true",
        "OPENCLAW_RESPONSES_URL": "http://100.64.0.1/responses",
        "OPENCLAW_RESPONSES_TOKEN_FILE": "/dev/null",
        "OPENCLAW_RESPONSES_STREAM_URL": "http://example.com/responses",
        "OPENCLAW_RESPONSES_STREAM_TOKEN_FILE": "/dev/null",
    }):
        get_webchat_fast_settings.cache_clear()
        with pytest.raises(RuntimeError, match="must point to a private or tailnet host"):
            get_webchat_fast_settings()

