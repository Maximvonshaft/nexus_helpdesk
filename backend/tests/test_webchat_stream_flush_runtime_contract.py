from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault('APP_ENV', 'development')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/webchat_stream_flush_runtime.db')
os.environ.setdefault('WEBCHAT_FAST_AI_ENABLED', 'false')

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import webchat_fast
from app.main import app
from app.services import webchat_fast_stream_service
from app.services.webchat_openclaw_stream_adapter import Completed, ContentDelta

pytestmark = pytest.mark.fast_lane_v2_2_2


def _settings():
    return SimpleNamespace(stream_enabled=True, stream_require_accept=True, openclaw_responses_agent_id='webchat-fast')


def _payload() -> dict:
    return {
        'tenant_key': 'default',
        'channel_key': 'website',
        'session_id': 'session-stream-flush',
        'client_message_id': 'client-stream-flush',
        'body': 'Hi',
        'recent_context': [],
    }


def test_first_reply_delta_is_observable_before_final(monkeypatch):
    async def fake_call_stream(**kwargs):
        yield ContentDelta('{"reply":"Hello')
        await asyncio.sleep(0.05)
        yield ContentDelta(' world","intent":"greeting","tracking_number":null,"handoff_required":false,"handoff_reason":null,"recommended_agent_action":null}')
        await asyncio.sleep(0.05)
        yield Completed(full_text='{"reply":"Hello world","intent":"greeting","tracking_number":null,"handoff_required":false,"handoff_reason":null,"recommended_agent_action":null}')

    monkeypatch.setattr(webchat_fast, 'get_webchat_fast_settings', _settings)
    monkeypatch.setattr(webchat_fast, 'enforce_webchat_fast_rate_limit', lambda *a, **k: None)
    monkeypatch.setattr(webchat_fast_stream_service.openclaw_client, 'call_openclaw_responses_stream', fake_call_stream)

    async def run():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url='http://testserver') as client:
            async with client.stream('POST', '/api/webchat/fast-reply/stream', json=_payload(), headers={'Accept': 'text/event-stream'}) as response:
                assert response.status_code == 200
                seen_reply_delta = False
                seen_final = False
                order = []
                async for chunk in response.aiter_text():
                    if 'event: reply_delta' in chunk and 'reply_delta' not in order:
                        order.append('reply_delta')
                        seen_reply_delta = True
                    if 'event: final' in chunk and 'final' not in order:
                        order.append('final')
                        seen_final = True
                assert seen_reply_delta is True
                assert seen_final is True
                assert order.index('reply_delta') < order.index('final')

    asyncio.run(run())
