from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault('APP_ENV', 'development')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/webchat_fast_stream_safety.db')
os.environ.setdefault('WEBCHAT_FAST_AI_ENABLED', 'false')

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.api import webchat_fast
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import BackgroundJob, Ticket
from app.services import webchat_fast_stream_service
from app.services.webchat_fast_idempotency_db import WebchatFastIdempotency
from app.services.webchat_openclaw_stream_adapter import Completed, ContentDelta, ToolCallDetected
from app.services.webchat_fast_stream_service import StreamBeginOutcome

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


def _payload(client_message_id: str = 'stream-safety-1') -> dict:
    return {
        'tenant_key': 'default',
        'channel_key': 'website',
        'session_id': 'session-stream-safety',
        'client_message_id': client_message_id,
        'body': 'Hi',
        'recent_context': [],
    }


def _parse_sse(body: str) -> list[tuple[str, dict]]:
    events = []
    for block in body.split('\n\n'):
        if not block.strip():
            continue
        event = 'message'
        data_lines = []
        for line in block.splitlines():
            if line.startswith('event:'):
                event = line.split(':', 1)[1].strip()
            elif line.startswith('data:'):
                data_lines.append(line.split(':', 1)[1].lstrip())
        if data_lines:
            events.append((event, json.loads('\n'.join(data_lines))))
    return events


def _settings():
    return SimpleNamespace(stream_enabled=True, stream_require_accept=True, openclaw_responses_agent_id='webchat-fast', is_openclaw_stream_configured=True)


def test_only_customer_visible_surfaces_are_exposed_and_final_intent_is_allowed(monkeypatch):
    async def fake_stream(**kwargs):
        yield 'event: meta\ndata: {"replayed":false}\n\n'
        yield 'event: reply_delta\ndata: {"text":"Hello"}\n\n'
        yield 'event: final\ndata: {"intent":"greeting","handoff_required":false,"ticket_creation_queued":false}\n\n'

    monkeypatch.setattr(webchat_fast, 'get_webchat_fast_settings', _settings)
    monkeypatch.setattr(webchat_fast, 'enforce_webchat_fast_rate_limit', lambda *a, **k: None)
    monkeypatch.setattr(webchat_fast, 'prepare_webchat_fast_stream', lambda **kwargs: StreamBeginOutcome(status='owner', request_hash='h', row_id=1))
    monkeypatch.setattr(webchat_fast, 'stream_webchat_fast_reply_events', fake_stream)

    response = client.post('/api/webchat/fast-reply/stream', json=_payload(), headers={'Accept': 'text/event-stream'})
    events = _parse_sse(response.text)
    visible = [payload['text'] for event, payload in events if event == 'reply_delta']
    replay = [payload['reply'] for event, payload in events if event == 'replay']
    finals = [payload for event, payload in events if event == 'final']
    assert visible == ['Hello']
    assert replay == []
    assert finals[0]['intent'] == 'greeting'
    assert 'reply' not in finals[0]


def test_stream_does_not_emit_reply_delta_until_final_parse_accepts(monkeypatch):
    early_valid_json = json.dumps(
        {
            'reply': 'Your parcel is still moving through our network.',
            'intent': 'tracking',
            'tracking_number': 'SPX123',
            'handoff_required': False,
            'handoff_reason': None,
            'recommended_agent_action': None,
        },
        separators=(',', ':'),
    )
    invalid_final_json = json.dumps({'reply': 'missing required business fields'}, separators=(',', ':'))

    async def fake_call_stream(**kwargs):
        yield ContentDelta(early_valid_json)
        yield Completed(full_text=invalid_final_json)

    monkeypatch.setattr(webchat_fast, 'get_webchat_fast_settings', _settings)
    monkeypatch.setattr(webchat_fast, 'enforce_webchat_fast_rate_limit', lambda *a, **k: None)
    monkeypatch.setattr(webchat_fast_stream_service.openclaw_client, 'call_openclaw_responses_stream', fake_call_stream)

    response = client.post('/api/webchat/fast-reply/stream', json=_payload('strict-final-gate'), headers={'Accept': 'text/event-stream'})
    events = _parse_sse(response.text)

    assert any(event == 'error' and payload.get('error_code') == 'ai_invalid_output' for event, payload in events)
    assert not any(event == 'reply_delta' for event, _ in events)
    assert not any(event == 'final' for event, _ in events)

    db = SessionLocal()
    try:
        row = db.execute(select(WebchatFastIdempotency).where(WebchatFastIdempotency.client_message_id == 'strict-final-gate')).scalar_one()
        assert row.status == 'failed'
        assert row.error_code == 'ai_invalid_output'
        assert db.execute(select(BackgroundJob)).scalars().all() == []
        assert db.execute(select(Ticket)).scalars().all() == []
    finally:
        db.close()


def test_stream_emits_single_full_reply_after_final_parse_accepts(monkeypatch):
    final_json = json.dumps(
        {
            'reply': 'Hello, I can help you check your shipment.',
            'intent': 'greeting',
            'tracking_number': None,
            'handoff_required': False,
            'handoff_reason': None,
            'recommended_agent_action': None,
        },
        separators=(',', ':'),
    )

    async def fake_call_stream(**kwargs):
        midpoint = len(final_json) // 2
        yield ContentDelta(final_json[:midpoint])
        yield ContentDelta(final_json[midpoint:])
        yield Completed(full_text=final_json)

    monkeypatch.setattr(webchat_fast, 'get_webchat_fast_settings', _settings)
    monkeypatch.setattr(webchat_fast, 'enforce_webchat_fast_rate_limit', lambda *a, **k: None)
    monkeypatch.setattr(webchat_fast_stream_service.openclaw_client, 'call_openclaw_responses_stream', fake_call_stream)

    response = client.post('/api/webchat/fast-reply/stream', json=_payload('strict-final-ok'), headers={'Accept': 'text/event-stream'})
    events = _parse_sse(response.text)

    reply_deltas = [payload['text'] for event, payload in events if event == 'reply_delta']
    finals = [payload for event, payload in events if event == 'final']

    assert reply_deltas == ['Hello, I can help you check your shipment.']
    assert len(finals) == 1
    assert finals[0]['intent'] == 'greeting'
    assert finals[0]['handoff_required'] is False

    db = SessionLocal()
    try:
        row = db.execute(select(WebchatFastIdempotency).where(WebchatFastIdempotency.client_message_id == 'strict-final-ok')).scalar_one()
        assert row.status == 'done'
        assert row.response_json['reply'] == 'Hello, I can help you check your shipment.'
    finally:
        db.close()


def test_tool_call_detected_aborts_without_reply_delta_or_side_effects(monkeypatch):
    async def fake_call_stream(**kwargs):
        yield ToolCallDetected('response.tool_call.delta')

    monkeypatch.setattr(webchat_fast, 'get_webchat_fast_settings', _settings)
    monkeypatch.setattr(webchat_fast, 'enforce_webchat_fast_rate_limit', lambda *a, **k: None)
    monkeypatch.setattr(webchat_fast_stream_service.openclaw_client, 'call_openclaw_responses_stream', fake_call_stream)

    response = client.post('/api/webchat/fast-reply/stream', json=_payload('tool-call'), headers={'Accept': 'text/event-stream'})
    events = _parse_sse(response.text)
    assert any(event == 'error' for event, _ in events)
    assert not any(event == 'reply_delta' for event, _ in events)

    db = SessionLocal()
    try:
        row = db.execute(select(WebchatFastIdempotency).where(WebchatFastIdempotency.client_message_id == 'tool-call')).scalar_one()
        assert row.status == 'failed'
        assert row.error_code in {'ai_unexpected_tool_call', 'ai_safety_abort'}
        from sqlalchemy import text as sql_text
        assert db.execute(sql_text('select count(*) from tickets')).scalar_one() == 0
        assert db.execute(sql_text('select count(*) from background_jobs')).scalar_one() == 0
    finally:
        db.close()


def test_stream_handoff_enqueue_failure_does_not_emit_reply_or_final_success(monkeypatch):
    final_json = json.dumps(
        {
            'reply': 'A human teammate will review this.',
            'intent': 'handoff',
            'tracking_number': None,
            'handoff_required': True,
            'handoff_reason': 'manual_review_required',
            'recommended_agent_action': 'Review this handoff request.',
        },
        separators=(',', ':'),
    )

    async def fake_call_stream(**kwargs):
        yield ContentDelta(final_json)
        yield Completed(full_text=final_json)

    def fail_enqueue(*args, **kwargs):
        raise RuntimeError('db unavailable')

    monkeypatch.setattr(webchat_fast, 'get_webchat_fast_settings', _settings)
    monkeypatch.setattr(webchat_fast, 'enforce_webchat_fast_rate_limit', lambda *a, **k: None)
    monkeypatch.setattr(webchat_fast_stream_service.openclaw_client, 'call_openclaw_responses_stream', fake_call_stream)
    monkeypatch.setattr(webchat_fast_stream_service, 'enqueue_webchat_handoff_snapshot_job', fail_enqueue)

    response = client.post('/api/webchat/fast-reply/stream', json=_payload('handoff-enqueue-failed'), headers={'Accept': 'text/event-stream'})
    events = _parse_sse(response.text)

    assert any(event == 'error' and payload.get('error_code') == 'handoff_enqueue_failed' for event, payload in events)
    assert not any(event == 'reply_delta' for event, _ in events)
    assert not any(event == 'final' for event, _ in events)

    db = SessionLocal()
    try:
        row = db.execute(select(WebchatFastIdempotency).where(WebchatFastIdempotency.client_message_id == 'handoff-enqueue-failed')).scalar_one()
        assert row.status == 'failed'
        assert row.error_code == 'handoff_enqueue_failed'
        assert db.execute(select(BackgroundJob)).scalars().all() == []
        assert db.execute(select(Ticket)).scalars().all() == []
    finally:
        db.close()
