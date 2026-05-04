from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import Base, engine, SessionLocal
from app.enums import UserRole
from app.models import BackgroundJob, User
from app.services.background_jobs import WEBCHAT_AI_REPLY_JOB, dispatch_pending_background_jobs
from app.webchat_models import WebchatConversation, WebchatMessage  # noqa: F401 - ensure metadata registration


@pytest.fixture(scope="module", autouse=True)
def ensure_schema():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if not db.query(User).filter(User.id == 9999).first():
            db.add(User(id=9999, username="roundb_admin", display_name="Round B Admin", password_hash="test", role=UserRole.admin, is_active=True))
            db.commit()
    finally:
        db.close()


def _create_webchat_message_flow(client: TestClient):
    init = client.post('/api/webchat/init', json={
        'tenant_key': 'pytest',
        'channel_key': 'website',
        'visitor_name': 'Pytest Visitor',
        'origin': 'https://example.test',
        'page_url': 'https://example.test/help',
    })
    assert init.status_code == 200, init.text
    payload = init.json()
    conversation_id = payload['conversation_id']
    visitor_token = payload['visitor_token']
    assert conversation_id.startswith('wc_')
    assert visitor_token

    sent = client.post(
        f'/api/webchat/conversations/{conversation_id}/messages',
        headers={'X-Webchat-Visitor-Token': visitor_token},
        json={
            'visitor_token': visitor_token,
            'body': 'Hello, what can you help me with?',
        },
    )
    assert sent.status_code == 200, sent.text

    polled = client.get(
        f'/api/webchat/conversations/{conversation_id}/messages',
        headers={'X-Webchat-Visitor-Token': visitor_token},
        params={'visitor_token': visitor_token},
    )
    assert polled.status_code == 200, polled.text
    payload_before = polled.json()
    messages_before = payload_before['messages']
    assert any(item['direction'] == 'visitor' and 'Hello, what can you help me with?' in item['body'] for item in messages_before)
    assert payload_before.get('ai_pending') is True
    assert payload_before.get('ai_status') in {'queued', 'processing', 'bridge_calling', 'fallback_generating'}
    return conversation_id, visitor_token


def _make_webchat_ai_jobs_due(db: SessionLocal) -> None:  # type: ignore[valid-type]
    jobs = db.query(BackgroundJob).filter(BackgroundJob.job_type == WEBCHAT_AI_REPLY_JOB, BackgroundJob.status == 'pending').all()
    assert jobs
    assert any(job.payload_json for job in jobs)
    for job in jobs:
        job.next_run_at = None
    db.commit()


def test_public_webchat_init_send_poll_and_background_ai_reply(monkeypatch):
    client = TestClient(app)
    conversation_id, visitor_token = _create_webchat_message_flow(client)

    from app.services import webchat_ai_service
    from app.services import webchat_ai_safe_service

    monkeypatch.setattr(webchat_ai_safe_service.settings, 'webchat_ai_auto_reply_mode', 'safe_ai')
    monkeypatch.setattr(webchat_ai_service, '_generate_ai_reply', lambda **kwargs: 'We can help with shipment questions, delivery updates, and general support requests.')

    db = SessionLocal()
    try:
        conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == conversation_id).first()
        assert conversation is not None
        _make_webchat_ai_jobs_due(db)
        processed = dispatch_pending_background_jobs(db, worker_id='pytest-worker')
        assert any(job.job_type == WEBCHAT_AI_REPLY_JOB for job in processed)
        db.commit()
    finally:
        db.close()

    polled_after = client.get(
        f'/api/webchat/conversations/{conversation_id}/messages',
        headers={'X-Webchat-Visitor-Token': visitor_token},
        params={'visitor_token': visitor_token},
    )
    assert polled_after.status_code == 200, polled_after.text
    messages_after = polled_after.json()['messages']
    assert any(item['direction'] == 'agent' and item['author_label'] == 'NexusDesk AI Assistant' for item in messages_after)
    assert any('shipment' in item['body'].lower() or 'support' in item['body'].lower() for item in messages_after if item['direction'] == 'agent')


def test_webchat_ai_reply_uses_bridge_when_enabled(monkeypatch):
    client = TestClient(app)
    conversation_id, visitor_token = _create_webchat_message_flow(client)

    from app.services import webchat_ai_service
    from app.services import webchat_ai_safe_service

    monkeypatch.setattr(webchat_ai_safe_service.settings, 'webchat_ai_auto_reply_mode', 'safe_ai')
    monkeypatch.setattr(webchat_ai_service.settings, 'openclaw_bridge_enabled', True)
    calls = []
    payloads = []

    def fake_urlopen(req, timeout=0):
        calls.append(req.full_url)
        if getattr(req, 'data', None):
            payloads.append((req.full_url, json.loads(req.data.decode('utf-8'))))
        class Resp:
            def __init__(self, payload):
                self.payload = payload
            def read(self):
                return json.dumps(self.payload).encode('utf-8')
            def __enter__(self):
                return self
            def __exit__(self, exc_type, exc, tb):
                return False
        if req.full_url.endswith('/ai-reply'):
            return Resp({'ok': True, 'messages': [{'role': 'assistant', 'text': 'We can help with delivery updates and general support.'}]})
        raise AssertionError(req.full_url)

    monkeypatch.setattr(webchat_ai_service.urllib.request, 'urlopen', fake_urlopen)

    db = SessionLocal()
    try:
        _make_webchat_ai_jobs_due(db)
        processed = dispatch_pending_background_jobs(db, worker_id='pytest-worker-bridge')
        assert any(job.job_type == WEBCHAT_AI_REPLY_JOB for job in processed)
        db.commit()
    finally:
        db.close()

    assert any(url.endswith('/ai-reply') for url in calls)
    assert not any(url.endswith('/send-message') for url in calls)

    ai_payload = next(payload for url, payload in payloads if url.endswith('/ai-reply'))
    assert ai_payload['sessionKey'].startswith(f'webchat-ai-{conversation_id}-')
    assert ai_payload['limit'] == 6
    assert isinstance(ai_payload['prompt'], str) and ai_payload['prompt']

    polled_after = client.get(
        f'/api/webchat/conversations/{conversation_id}/messages',
        headers={'X-Webchat-Visitor-Token': visitor_token},
        params={'visitor_token': visitor_token},
    )
    assert polled_after.status_code == 200, polled_after.text
    messages_after = polled_after.json()['messages']
    assert any(item['direction'] == 'agent' and item['author_label'] == 'NexusDesk AI Assistant' for item in messages_after)


def test_bridge_ai_reply_maps_public_session_key_to_gateway_key():
    bridge_script = Path(__file__).resolve().parents[1] / 'scripts' / 'openclaw_bridge_server.js'
    source = bridge_script.read_text(encoding='utf-8')
    ai_reply_block = source.split('async aiReply(payload) {', 1)[1].split('\n  pollEvents(payload)', 1)[0]

    assert ('sessions.send' in ai_reply_block) or ("['sessions', 'send'].join('.')" in ai_reply_block)
    assert "this.client.request('chat.history'" in ai_reply_block
    assert 'sessionKey,\n        message: prompt' not in ai_reply_block
    assert 'sessionKey,\n        limit,' not in ai_reply_block


def test_bridge_ai_reply_is_decoupled_from_external_write_mode():
    bridge_script = Path(__file__).resolve().parents[1] / 'scripts' / 'openclaw_bridge_server.js'
    source = bridge_script.read_text(encoding='utf-8')
    send_block = source.split('async sendMessage(payload) {', 1)[1].split('\n  async listConversations', 1)[0]
    ai_reply_block = source.split('async aiReply(payload) {', 1)[1].split('\n  async lookupSpeedaf', 1)[0]
    health_block = source.split('health() {', 1)[1].split('\n  async stop()', 1)[0]

    assert "allowWrites: truthyEnv('OPENCLAW_BRIDGE_ALLOW_WRITES', false)" in source
    assert "aiReplyEnabled: truthyEnv('OPENCLAW_BRIDGE_AI_REPLY_ENABLED', true)" in source
    assert "if (!this.config.allowWrites) throw new Error('bridge_writes_disabled');" in send_block
    assert "if (!this.config.allowWrites)" not in ai_reply_block
    assert "if (!this.config.aiReplyEnabled) throw new Error('bridge_ai_reply_disabled');" in ai_reply_block
    assert 'bridge_ai_reply_disabled' in ai_reply_block
    assert 'bridge_writes_disabled' not in ai_reply_block
    assert 'bridge_writes_disabled' in send_block
    assert 'allowWrites: this.config.allowWrites' in health_block
    assert 'aiReplyEnabled: this.config.aiReplyEnabled' in health_block
    assert 'sendMessageEnabled: this.config.allowWrites' in health_block


def test_webchat_ai_reply_bridge_failure_falls_back_safely(monkeypatch):
    client = TestClient(app)
    init = client.post('/api/webchat/init', json={
        'tenant_key': 'pytest',
        'channel_key': 'website',
        'visitor_name': 'Pytest Visitor',
        'origin': 'https://example.test',
        'page_url': 'https://example.test/help',
    })
    payload = init.json()
    conversation_id = payload['conversation_id']
    visitor_token = payload['visitor_token']
    sent = client.post(
        f'/api/webchat/conversations/{conversation_id}/messages',
        headers={'X-Webchat-Visitor-Token': visitor_token},
        json={
            'visitor_token': visitor_token,
            'body': 'Where is my parcel?',
        },
    )
    assert sent.status_code == 200, sent.text

    from app.services import webchat_ai_service
    from app.services import webchat_ai_safe_service

    monkeypatch.setattr(webchat_ai_safe_service.settings, 'webchat_ai_auto_reply_mode', 'safe_ai')
    monkeypatch.setattr(webchat_ai_service.settings, 'openclaw_bridge_enabled', True)

    def fake_urlopen_fail(req, timeout=0):
        raise RuntimeError('bridge down')

    monkeypatch.setattr(webchat_ai_service.urllib.request, 'urlopen', fake_urlopen_fail)

    db = SessionLocal()
    try:
        _make_webchat_ai_jobs_due(db)
        processed = dispatch_pending_background_jobs(db, worker_id='pytest-worker-bridge-fail')
        assert any(job.job_type == WEBCHAT_AI_REPLY_JOB for job in processed)
        db.commit()
    finally:
        db.close()

    polled_after = client.get(
        f'/api/webchat/conversations/{conversation_id}/messages',
        headers={'X-Webchat-Visitor-Token': visitor_token},
        params={'visitor_token': visitor_token},
    )
    assert polled_after.status_code == 200, polled_after.text
    messages_after = polled_after.json()['messages']
    assert any(item['direction'] == 'visitor' and 'Where is my parcel?' in item['body'] for item in messages_after)
    assert any(item['direction'] == 'agent' for item in messages_after)
    assert any(item['direction'] == 'agent' and item['author_label'] == 'NexusDesk AI Assistant' for item in messages_after)
    assert any('tracking number' in item['body'].lower() or 'next step' in item['body'].lower() or 'available information' in item['body'].lower() for item in messages_after if item['author_label'] == 'NexusDesk AI Assistant')
