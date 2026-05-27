from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import webchat as webchat_api
from app.main import app
from app.db import Base, engine, SessionLocal
from app.enums import SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole
from app.models import BackgroundJob, Customer, Ticket, User
from app.services import background_jobs, webchat_rate_limit
from app.services.background_jobs import WEBCHAT_AI_REPLY_JOB
from app.webchat_models import WebchatConversation, WebchatMessage  # noqa: F401 - ensure metadata registration

ALLOWED_PUBLIC_POLL_ORIGIN = "https://www.leakle.com"
PUBLIC_POLL_VISITOR_TOKEN = "visitor-token-public-poll-origin"


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
    messages_before = polled.json()['messages']
    assert any(item['direction'] == 'visitor' and 'Hello, what can you help me with?' in item['body'] for item in messages_before)
    assert any(item['direction'] == 'agent' and 'received your message' in item['body'] for item in messages_before)
    return conversation_id, visitor_token


@pytest.fixture()
def production_public_poll_settings(monkeypatch):
    monkeypatch.setattr(webchat_api.settings, "app_env", "production")
    monkeypatch.setattr(webchat_api.settings, "webchat_allowed_origins", [ALLOWED_PUBLIC_POLL_ORIGIN])
    monkeypatch.setattr(webchat_api.settings, "webchat_allow_no_origin", False)
    monkeypatch.setattr(webchat_rate_limit.settings, "webchat_rate_limit_backend", "memory")


def _create_public_poll_conversation(visitor_token: str = PUBLIC_POLL_VISITOR_TOKEN) -> str:
    suffix = uuid.uuid4().hex[:12]
    public_id = f"wcf_poll_{suffix}"
    db = SessionLocal()
    try:
        customer = Customer(name="Public Poll Visitor", email=f"poll-{suffix}@example.invalid")
        db.add(customer)
        db.flush()
        ticket = Ticket(
            ticket_no=f"WC-POLL-{suffix.upper()}",
            title="Public poll origin fixture",
            description="Fixture for public webchat polling",
            customer_id=customer.id,
            source=TicketSource.user_message,
            source_channel=SourceChannel.web_chat,
            priority=TicketPriority.medium,
            status=TicketStatus.in_progress,
        )
        db.add(ticket)
        db.flush()
        conversation = WebchatConversation(
            public_id=public_id,
            visitor_token_hash=hashlib.sha256(visitor_token.encode("utf-8")).hexdigest(),
            tenant_key="default",
            channel_key="website",
            ticket_id=ticket.id,
            visitor_name="Public Poll Visitor",
            visitor_email=customer.email,
            origin=ALLOWED_PUBLIC_POLL_ORIGIN,
            status="open",
            handoff_status="requested",
        )
        db.add(conversation)
        db.flush()
        db.add(
            WebchatMessage(
                conversation_id=conversation.id,
                ticket_id=ticket.id,
                direction="agent",
                body="Support reply",
                body_text="Support reply",
                message_type="text",
                author_label="Support",
            )
        )
        db.commit()
        return public_id
    finally:
        db.close()


def _poll_public(client: TestClient, public_id: str, token: str = PUBLIC_POLL_VISITOR_TOKEN, headers: dict[str, str] | None = None):
    request_headers = {"X-Webchat-Visitor-Token": token}
    request_headers.update(headers or {})
    return client.get(f"/api/webchat/conversations/{public_id}/messages?limit=5", headers=request_headers)


def test_public_poll_without_origin_or_referer_allows_valid_visitor_token(production_public_poll_settings):
    client = TestClient(app)
    public_id = _create_public_poll_conversation()

    response = _poll_public(client, public_id)

    assert response.status_code == 200
    payload = response.json()
    assert payload["conversation_id"] == public_id
    assert payload["handoff_status"] == "requested"
    assert payload["messages"][0]["body_text"] == "Support reply"
    assert "access-control-allow-origin" not in response.headers


def test_public_poll_without_origin_or_referer_rejects_invalid_visitor_token(production_public_poll_settings):
    client = TestClient(app)
    public_id = _create_public_poll_conversation()

    response = _poll_public(client, public_id, token="wrong-token")

    assert response.status_code == 403
    assert response.json()["detail"] == "invalid webchat visitor token"


def test_public_poll_rejects_disallowed_origin_before_token_result(production_public_poll_settings):
    client = TestClient(app)
    public_id = _create_public_poll_conversation()

    response = _poll_public(client, public_id, headers={"Origin": "https://evil.example"})

    assert response.status_code == 403
    assert response.json()["detail"] == "Webchat origin is not allowed"


def test_public_poll_allows_allowed_origin_with_valid_visitor_token(production_public_poll_settings):
    client = TestClient(app)
    public_id = _create_public_poll_conversation()

    response = _poll_public(client, public_id, headers={"Origin": ALLOWED_PUBLIC_POLL_ORIGIN})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_PUBLIC_POLL_ORIGIN
    assert response.json()["messages"][0]["body_text"] == "Support reply"


def test_public_poll_validates_referer_origin(production_public_poll_settings):
    client = TestClient(app)
    public_id = _create_public_poll_conversation()

    allowed = _poll_public(client, public_id, headers={"Referer": f"{ALLOWED_PUBLIC_POLL_ORIGIN}/support/chat"})
    rejected = _poll_public(client, public_id, headers={"Referer": "https://evil.example/support/chat"})

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == ALLOWED_PUBLIC_POLL_ORIGIN
    assert rejected.status_code == 403
    assert rejected.json()["detail"] == "Webchat origin is not allowed"


def test_public_poll_missing_conversation_remains_404_after_token_resolution(production_public_poll_settings):
    client = TestClient(app)

    response = _poll_public(client, "wcf_missing_public_poll")

    assert response.status_code == 404
    assert response.json()["detail"] == "webchat conversation not found"


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
        jobs = db.query(BackgroundJob).filter(BackgroundJob.job_type == WEBCHAT_AI_REPLY_JOB, BackgroundJob.status == 'pending').all()
        assert jobs
        current_job = jobs[-1]
        assert current_job.payload_json
        background_jobs.process_background_job(db, current_job)
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
    assert any(item['direction'] == 'agent' and item['author_label'] == 'AI Assistant' for item in messages_after)
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
        job = (
            db.query(BackgroundJob)
            .filter(BackgroundJob.job_type == WEBCHAT_AI_REPLY_JOB, BackgroundJob.status == 'pending')
            .order_by(BackgroundJob.id.desc())
            .first()
        )
        assert job is not None
        background_jobs.process_background_job(db, job)
        db.commit()
    finally:
        db.close()

    assert any(url.endswith('/ai-reply') for url in calls)
    assert not any(url.endswith('/send-message') for url in calls)

    ai_payload = next(payload for url, payload in payloads if url.endswith('/ai-reply'))
    assert ai_payload['sessionKey'] == f'webchat:pytest:website:{conversation_id}'
    assert ai_payload['limit'] == 6
    assert isinstance(ai_payload['prompt'], str) and ai_payload['prompt']

    polled_after = client.get(
        f'/api/webchat/conversations/{conversation_id}/messages',
        headers={'X-Webchat-Visitor-Token': visitor_token},
        params={'visitor_token': visitor_token},
    )
    assert polled_after.status_code == 200, polled_after.text
    messages_after = polled_after.json()['messages']
    assert any(item['direction'] == 'agent' and item['author_label'] == 'AI Assistant' for item in messages_after)


def test_bridge_ai_reply_maps_public_session_key_to_gateway_key():
    bridge_script = Path(__file__).resolve().parents[1] / 'scripts' / 'openclaw_bridge_server.js'
    source = bridge_script.read_text(encoding='utf-8')
    ai_reply_block = source.split('async aiReply(payload) {', 1)[1].split('\n  pollEvents(payload)', 1)[0]

    assert "this.client.request('chat.send'" in ai_reply_block
    assert "this.client.request('chat.history'" in ai_reply_block
    assert 'toAgentScopedSessionKey(requestedSessionKey, this.config.aiReplyAgentId)' in ai_reply_block
    assert 'sessionKey: effectiveSessionKey' in ai_reply_block
    assert "this.client.request(['sessions', 'send'].join('.')," not in ai_reply_block
    assert 'key: sessionKey' not in ai_reply_block
    assert 'agent:support:main' not in ai_reply_block


def test_bridge_ai_reply_is_decoupled_from_external_write_mode():
    bridge_script = Path(__file__).resolve().parents[1] / 'scripts' / 'openclaw_bridge_server.js'
    source = bridge_script.read_text(encoding='utf-8')
    send_block = source.split('async sendMessage(payload) {', 1)[1].split('\n  async listConversations', 1)[0]
    ai_reply_block = source.split('async aiReply(payload) {', 1)[1].split('\n  pollEvents(payload)', 1)[0]
    health_block = source.split('health() {', 1)[1].split('\n  async stop()', 1)[0]

    assert "allowWrites: truthyEnv('OPENCLAW_BRIDGE_ALLOW_WRITES', false)" in source
    assert "aiReplyEnabled: truthyEnv('OPENCLAW_BRIDGE_AI_REPLY_ENABLED', true)" in source
    assert "if (!this.config.allowWrites) throw new Error('bridge_writes_disabled');" in send_block
    assert "if (!this.config.allowWrites)" not in ai_reply_block
    assert "if (!this.config.aiReplyEnabled) throw new Error('bridge_ai_reply_disabled');" in ai_reply_block
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
        job = (
            db.query(BackgroundJob)
            .filter(BackgroundJob.job_type == WEBCHAT_AI_REPLY_JOB, BackgroundJob.status == 'pending')
            .order_by(BackgroundJob.id.desc())
            .first()
        )
        assert job is not None
        background_jobs.process_background_job(db, job)
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
    assert any(item['direction'] == 'agent' and 'received your parcel inquiry' in item['body'] for item in messages_after)
    assert any(item['direction'] == 'agent' and item['author_label'] == 'AI Assistant' for item in messages_after)
    assert any('tracking number' in item['body'].lower() or 'review' in item['body'].lower() for item in messages_after if item['author_label'] == 'AI Assistant')
